import sys
import json
from recipe_scrapers import scrape_me
from pint import UnitRegistry, UndefinedUnitError

# --- Initialization ---
ureg = UnitRegistry()

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

def scrape_to_markdown(url):
    """Scrapes a recipe URL and prints the recipe in a structured Markdown format."""
    try:
        # Initialize scraper
        scraper = scrape_me(url)
        
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
        return True
        
    except Exception as e:
        print(f"Error scraping recipe: {e}", file=sys.stderr)
        sys.exit(1)

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
