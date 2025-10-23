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
    
    target_unit_map = {
        'metric': 'milliliter',
        'imperial': 'cup'
    }
    
    target_unit_str = target_unit_map.get(target_system.lower())
    
    if not target_unit_str:
        return ingredients_list, "Invalid target system. Use 'metric' or 'imperial'."

    for line in ingredients_list:
        try:
            q = ureg.Quantity(line)
            
            is_volume_or_mass = (q.dimensionality == ureg.ounce.dimensionality or 
                                ureg.check('[volume]') in q.dimensionality or
                                ureg.check('[mass]') in q.dimensionality)

            if is_volume_or_mass:
                original_unit_str = str(q.units)
                remaining_description = line.split(original_unit_str, 1)[-1].strip()
                
                if target_system.lower() == 'metric':
                    converted_q = q.to_base_units()
                    converted_line = f"{converted_q:~P} {remaining_description}"
                else: 
                    converted_q = q.to(target_unit_str)
                    converted_line = f"{converted_q:~P} {remaining_description}"
            else:
                converted_line = line
                
        except UndefinedUnitError:
            converted_line = line
        except Exception:
            converted_line = line

        converted_list.append(converted_line)

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
    """Scrapes a recipe URL and prints the recipe in a structured Markdown format.
    Tries recipe-scrapers first, then JSON-LD/microdata, then a lightweight HTML parser.
    Returns 0 on success, 1 on error.
    """
    clean_url = url.split('#')[0]

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

    except Exception as primary_err:
        # Continue to fallback parsing
        pass

    # Fetch page and parse
    try:
        resp = requests.get(clean_url, timeout=10, headers={'User-Agent': 'tempura/1.0'})
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
        print(f"Error scraping recipe: {primary_err}  |  fallback failed: {fallback_err}", file=sys.stderr)
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
