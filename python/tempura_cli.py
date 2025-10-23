import sys
import json
import re
from recipe_scrapers import scrape_me
from pint import UnitRegistry, UndefinedUnitError

# add these imports for the fallback scraper
import requests
from bs4 import BeautifulSoup

# --- Initialization ---
ureg = UnitRegistry()

# --- Helpers ---
_fraction_re = re.compile(r'^\d+\s+\d+/\d+$|^\d+/\d+$|^\d+(?:\.\d+)?$')

def _parse_amount_unit_description(line):
    """
    Very-lightweight heuristic parser.
    Returns (amount_str, unit_str, description) or (None, None, line) if parsing fails.
    Handles:
      - "1 cup sugar"
      - "1 1/2 cups flour"
      - "3/4 tsp salt"
      - "1 (14 oz) can tomatoes" -> falls back to original line
    """
    if not line or not line.strip():
        return None, None, line

    tokens = line.strip().split()
    if not tokens:
        return None, None, line

    # identify amount (supports mixed numbers like "1 1/2")
    amount_tokens = []
    idx = 0
    if _fraction_re.match(tokens[0]):
        amount_tokens.append(tokens[0])
        idx = 1
        # mixed number like "1 1/2"
        if idx < len(tokens) and re.match(r'^\d+/\d+$', tokens[idx]):
            amount_tokens.append(tokens[idx])
            idx += 1
    else:
        # first token is not a plain number/fraction -> give up
        return None, None, line

    amount_str = ' '.join(amount_tokens)

    # next token as unit if present and looks like a unit (letters or %)
    if idx < len(tokens):
        unit_candidate = tokens[idx].strip().rstrip('.,;:')
        # avoid capturing parenthetical or things like "can", "package" as units for conversion
        if re.search(r'[A-Za-z%/]+', unit_candidate):
            unit_str = unit_candidate
            idx += 1
        else:
            return None, None, line
    else:
        return None, None, line

    description = ' '.join(tokens[idx:])

    return amount_str, unit_str, description

# --- Unit Conversion Logic ---

def convert_ingredients(ingredients_list, target_system):
    """Converts a list of ingredient strings to the target unit system (metric or imperial)."""
    converted_list = []

    target_system = target_system.lower()
    if target_system not in ('metric', 'imperial'):
        return ingredients_list, "Invalid target system. Use 'metric' or 'imperial'."

    # simple alias map for common units (expanded)
    unit_alias = {
        'tbs': 'tablespoon', 'tbsp': 'tablespoon', 'tbsp.': 'tablespoon', 'tablespoons': 'tablespoon',
        'tsp': 'teaspoon', 'tsp.': 'teaspoon', 'tsps': 'teaspoon',
        'cup': 'cup', 'cups': 'cup',
        'oz': 'ounce', 'ounce': 'ounce', 'ounces': 'ounce',
        'lb': 'pound', 'lbs': 'pound', 'pound': 'pound',
        'g': 'gram', 'gram': 'gram', 'grams': 'gram',
        'kg': 'kilogram', 'ml': 'milliliter', 'l': 'liter',
        'clove': 'count', 'cloves': 'count', 'slice': 'count'
    }

    # helper: expand common unicode vulgar fractions (½ ⅓ ¼ etc) to ascii fractions
    def _normalize_unicode_fractions(s):
        if not s:
            return s
        frac_map = {
            '½': '1/2', '⅓': '1/3', '⅔': '2/3', '¼': '1/4', '¾': '3/4',
            '⅛': '1/8', '⅜': '3/8', '⅝': '5/8', '⅞': '7/8'
        }
        for sym, rep in frac_map.items():
            # turn "1½" -> "1 1/2"
            s = re.sub(r'(\d)'+re.escape(sym), r'\1 ' + rep, s)
            # turn "½ cup" -> "1/2 cup"
            s = s.replace(sym, rep)
        return s

    # regex to capture amount (mixed numbers, fractions, decimals) + unit + rest
    amt_unit_re = re.compile(r'^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+\.\d+|\d+)\s*([A-Za-z%\.]+)?\.?\s*(.*)$')

    for line in ingredients_list:
        try:
            if not line or not line.strip():
                converted_list.append(line)
                continue

            # remove simple markdown bullets like "* " or "- "
            line = re.sub(r'^\s*[\*\-]\s*', '', line).strip()

            # normalize unicode fractions so "1½" etc are parsed
            line = _normalize_unicode_fractions(line)

            m = amt_unit_re.match(line)
            if not m:
                converted_list.append(line)
                continue

            amt_str, unit_token, desc = m.groups()
            desc = desc.strip() if desc else ''

            # normalize amount (mixed numbers, fractions)
            amount_norm = None
            if ' ' in amt_str and '/' in amt_str:
                # mixed number e.g. "1 1/2"
                whole, frac = amt_str.split()
                num, den = frac.split('/')
                amount_norm = float(whole) + float(num) / float(den)
            elif '/' in amt_str and '.' not in amt_str:
                num, den = amt_str.split('/')
                amount_norm = float(num) / float(den)
            else:
                amount_norm = float(amt_str)

            if not unit_token:
                converted_list.append(line)
                continue

            # strip trailing punctuation from unit token (e.g. "tbsp.")
            unit_token = unit_token.rstrip('.,;:')
            unit_key = unit_token.lower()
            unit_name = unit_alias.get(unit_key, unit_key)

            # Build pint quantity
            try:
                q = ureg.Quantity(amount_norm, unit_name)
            except Exception:
                # last attempt: let pint parse expression
                try:
                    q = ureg.parse_expression(f"{amount_norm} {unit_name}")
                except Exception:
                    converted_list.append(line)
                    continue

            dim = q.dimensionality

            # choose sensible target unit
            if '[volume]' in str(dim) or dim == ureg.milliliter.dimensionality or dim == ureg.liter.dimensionality:
                target_unit = 'milliliter' if target_system == 'metric' else 'cup'
            elif '[mass]' in str(dim) or dim == ureg.gram.dimensionality or dim == ureg.kilogram.dimensionality:
                target_unit = 'gram' if target_system == 'metric' else 'ounce'
            else:
                # dimensionless or count -> leave unchanged
                converted_list.append(line)
                continue

            # perform conversion
            try:
                converted_q = q.to(target_unit)
            except Exception:
                converted_list.append(line)
                continue

            # format magnitude smartly
            mag = converted_q.magnitude
            if abs(mag - round(mag)) < 1e-6:
                mag_str = str(int(round(mag)))
            else:
                mag_str = f"{round(mag, 2):.2f}".rstrip('0').rstrip('.')

            # short unit names map
            short_unit_map = {
                'milliliter': 'ml', 'liter': 'l', 'gram': 'g', 'kilogram': 'kg',
                'ounce': 'oz', 'pound': 'lb', 'cup': 'cup'
            }
            unit_out = short_unit_map.get(str(converted_q.units), str(converted_q.units))

            if desc:
                converted_line = f"{mag_str} {unit_out} {desc}"
            else:
                converted_line = f"{mag_str} {unit_out}"

            converted_list.append(converted_line)

        except Exception:
            # On any unexpected error, keep original line
            converted_list.append(line)

    return converted_list, None

# --- Scraping Logic ---

def _extract_from_jsonld(soup):
    """Return (title, ingredients_list, instructions_list) or (None, None, None)."""
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
        except Exception:
            continue

        # JSON-LD can be a list or single object
        items = data if isinstance(data, list) else [data]
        for item in items:
            # sometimes nested under '@graph'
            if isinstance(item, dict) and '@graph' in item:
                graph = item.get('@graph') or []
                if isinstance(graph, list):
                    items.extend(graph)

        for obj in items:
            if not isinstance(obj, dict):
                continue
            typ = obj.get('@type') or obj.get('type') or ''
            if isinstance(typ, list):
                is_recipe = 'Recipe' in typ
            else:
                is_recipe = typ and 'recipe' in str(typ).lower() or typ == 'Recipe'
            if not is_recipe:
                # sometimes name is present with recipeIngredient key even if @type missing
                if 'recipeIngredient' not in obj and 'ingredients' not in obj:
                    continue

            title = obj.get('name') or obj.get('headline')
            ingredients = obj.get('recipeIngredient') or obj.get('ingredients') or []
            # normalize instructions
            raw_instructions = obj.get('recipeInstructions') or obj.get('instructions') or []
            instructions = []
            if isinstance(raw_instructions, str):
                # split on newlines or sentences heuristically
                for ln in re.split(r'\n+', raw_instructions):
                    if ln.strip():
                        instructions.append(ln.strip())
            elif isinstance(raw_instructions, list):
                for step in raw_instructions:
                    if isinstance(step, str):
                        if step.strip():
                            instructions.append(step.strip())
                    elif isinstance(step, dict):
                        # Could be HowToStep or similar
                        text = step.get('text') or step.get('description') or ''
                        if text:
                            instructions.append(text.strip())
            # final normalize ingredients to strings
            ingredients = [str(x).strip() for x in ingredients if str(x).strip()]
            if ingredients:
                return title or 'Recipe', ingredients, instructions
    return None, None, None

def scrape_to_markdown(url):
    """Scrapes a recipe URL and prints the recipe in a structured Markdown format."""
    clean_url = url.split('#')[0]
    primary_error = None

    # First try recipe-scrapers
    try:
        scraper = scrape_me(clean_url)
        md = f"# {scraper.title()}\n\n"
        md += f"Source: <{url}>\n\n"
        md += "---\n\n"

        md += "## Ingredients 🧂\n\n"
        for ing in scraper.ingredients():
            md += f"* {ing}\n"

        md += "\n## Instructions 🔪\n\n"
        instructions = scraper.instructions().split('\n')

        step_number = 1
        for step in instructions:
            if step.strip():
                md += f"{step_number}. {step.strip()}\n"
                step_number += 1

        print(md)
        return 0

    except Exception as e:
        primary_error = str(e)
        # Continue to fallback parsing
        pass

    # Fetch page and parse
    try:
        resp = requests.get(clean_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Try JSON-LD / structured data first
        title, ingredients, instructions = _extract_from_jsonld(soup)
        if ingredients:
            md = f"# {title or 'Recipe'}\n\n"
            md += f"Source: <{url}>\n\n"
            md += "---\n\n"
            md += "## Ingredients 🧂\n\n"
            for ing in ingredients:
                md += f"* {ing}\n"
            if instructions:
                md += "\n## Instructions 🔪\n\n"
                for i, step in enumerate(instructions, 1):
                    md += f"{i}. {step}\n"
            print(md)
            return 0

        # Heuristic selectors (expanded)
        ingredients = []
        selectors = [
            '[class*="ingredient"]',
            '[id*="ingredient"]',
            '[class*="ingredients"]',
            '[id*="ingredients"]',
            '[class*="ingre"]',
            '[id*="ingre"]',
            '[class*="recipe-ingredients"]',
            '[id*="recipe-ingredients"]',
            '[data-ingredient]',
        ]
        for sel in selectors:
            for node in soup.select(sel):
                # prefer list items
                lis = node.find_all('li')
                if lis:
                    for li in lis:
                        txt = li.get_text(' ', strip=True)
                        if txt:
                            ingredients.append(txt)
                    continue
                # else split lines within node
                text = node.get_text('\n').strip()
                for ln in text.splitlines():
                    if ln.strip():
                        ingredients.append(ln.strip())

        # check headers with 'ingredient'
        for hdr in soup.find_all(['h2', 'h3', 'h4'], string=re.compile('ingredient', re.I)):
            ul = hdr.find_next(['ul', 'ol'])
            if ul:
                for li in ul.find_all('li'):
                    txt = li.get_text(' ', strip=True)
                    if txt:
                        ingredients.append(txt)
            else:
                node = hdr.find_next_sibling()
                steps = 0
                while node and steps < 30:
                    txt = node.get_text(' ', strip=True)
                    if txt:
                        ingredients.append(txt)
                    node = node.find_next_sibling()
                    steps += 1

        # dedupe preserve order
        seen = set()
        ingredients = [x for x in ingredients if x and not (x in seen or seen.add(x))]

        # instructions heuristic
        instructions = []
        for hdr in soup.find_all(['h2', 'h3', 'h4'], string=re.compile('instruction|direction|method|preparation|step', re.I)):
            ol = hdr.find_next(['ol', 'ul'])
            if ol:
                for li in ol.find_all('li'):
                    txt = li.get_text(' ', strip=True)
                    if txt:
                        instructions.append(txt)
            else:
                node = hdr.find_next_sibling()
                steps = 0
                while node and steps < 30:
                    if node.name == 'p':
                        txt = node.get_text(' ', strip=True)
                        if txt:
                            instructions.append(txt)
                    node = node.find_next_sibling()
                    steps += 1

        # as last resort, try to find long text blocks that look like ingredients/instructions
        if not ingredients:
            # look for 'ul' elements near recipe-like headers
            for ul in soup.find_all('ul'):
                txt = ' '.join(li.get_text(' ', strip=True) for li in ul.find_all('li'))
                if txt and len(txt) < 2000 and re.search(r'\b(tsp|tbsp|cup|ounce|gram|kg|g|ml|mls)\b', txt, re.I):
                    for li in ul.find_all('li'):
                        t = li.get_text(' ', strip=True)
                        if t:
                            ingredients.append(t)
                if ingredients:
                    break

        if not ingredients:
            # Extra debug info
            print(f"Debug: Found {len(soup.find_all('ul'))} ul elements", file=sys.stderr)
            print(f"Debug: Found {len(soup.find_all('li'))} li elements", file=sys.stderr)
            
            # More aggressive ingredient finding: scan paragraphs, table cells and all visible text lines
            candidate_lines = []

            # paragraphs and table cells
            for node in soup.find_all(['p', 'td', 'th', 'li']):
                txt = node.get_text(' ', strip=True)
                if txt and len(txt) < 2000:
                    candidate_lines.extend([ln.strip() for ln in re.split(r'[\r\n]+', txt) if ln.strip()])

            # larger content blocks (entry/article)
            for sel in ['article', '[class*="entry"]', '[class*="content"]', '[id*="content"]', '[class*="recipe"]']:
                for node in soup.select(sel):
                    txt = node.get_text('\n').strip()
                    if txt:
                        candidate_lines.extend([ln.strip() for ln in txt.splitlines() if ln.strip()])

            # fallback to whole body text split into lines
            if not candidate_lines:
                body_txt = soup.get_text('\n').strip()
                candidate_lines = [ln.strip() for ln in body_txt.splitlines() if ln.strip()]

            # scoring: prefer lines that contain a number or cooking measurement words
            unit_pattern = re.compile(r'\b(\d+(/\d+)?|\d+\.\d+|cup|cups|tbsp|tablespoon|tablespoons|tsp|teaspoon|teaspoons|oz|ounce|ounces|gram|grams|g|kg|ml|clove|pinch|pound|lb|slice)\b', re.I)
            for ln in candidate_lines:
                if unit_pattern.search(ln):
                    # ignore very long lines
                    if len(ln) < 300:
                        ingredients.append(ln)

            # try to dedupe while preserving order
            if ingredients:
                seen = set()
                ingredients = [x for x in ingredients if x and not (x in seen or seen.add(x))]

        if not ingredients:
            raise Exception("Fallback scraping couldn't find ingredients")

        # build markdown
        md = f"# {soup.find('h1').get_text(strip=True) if soup.find('h1') else 'Recipe'}\n\n"
        md += f"Source: <{url}>\n\n"
        md += "---\n\n"

        md += "## Ingredients 🧂\n\n"
        for ing in ingredients:
            md += f"* {ing}\n"

        if instructions:
            md += "\n## Instructions 🔪\n\n"
            for i, step in enumerate(instructions, 1):
                md += f"{i}. {step}\n"

        print(md)
        return 0

    except Exception as fallback_err:
        print(f"Error scraping recipe: {primary_error} | Fallback failed: {str(fallback_err)}", file=sys.stderr)
        return 1

# --- Main CLI Entry Point ---

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Error: Insufficient arguments", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1].lower()
    
    if command == "scrape":
        url = sys.argv[2]
        scrape_to_markdown(url)
    
    elif command == "convert":
        if len(sys.argv) < 4:
            print("Error: Missing arguments for convert command", file=sys.stderr)
            sys.exit(1)
            
        target_system = sys.argv[2]
        ingredients_json = sys.argv[3] 
        
        try:
            ingredients_list = json.loads(ingredients_json)
            if not isinstance(ingredients_list, list):
                raise ValueError("Input must be a JSON array of strings")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error decoding JSON input: {e}", file=sys.stderr)
            sys.exit(1)
        
        converted, error = convert_ingredients(ingredients_list, target_system)
        
        if error:
            print(f"Conversion Error: {error}", file=sys.stderr)
            sys.exit(1)
            
        print(json.dumps(converted)) 
        
    else:
        print(f"Error: Unknown command '{command}'", file=sys.stderr)
        sys.exit(1)
