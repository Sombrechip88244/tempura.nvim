# Tempura.nvim 🍤

A Neovim plugin for seamless recipe management. Scrape recipes from the web and save them as structured Markdown files, complete with the ability to convert ingredient units between metric and imperial systems using Python.

## ✨ Features

 - Web Scraping: Extract recipes (title, ingredients, instructions) from major recipe sites directly into a new Neovim buffer.

 - Structured Markdown: Recipes are saved in a consistent Markdown format.

- Unit Conversion: Convert ingredient quantities between Metric (grams, milliliters) and Imperial (cups, ounces) systems on the fly.

## 📦 Installation (using lazy.nvim)

Tempura.nvim requires **Python 3** and specific libraries (recipe-scrapers and pint) to handle the heavy lifting of scraping and unit conversion.

To ensure these dependencies are installed automatically when you install the plugin, use the following lazy.nvim specification in your configuration (e.g., in ~/.config/nvim/lua/plugins/init.lua):

#### Prerequisites

**Python 3** installed on your system.

`pip` (Python package installer).

#### Lazy Spec
```lua
{
  "Sombrechip88244/tempura.nvim",
  lazy = false,
  
  build = function()
    -- NOTE: This assumes 'python3' and 'pip' are available in your PATH.
    vim.fn.system({"python3", "-m", "pip", "install", "-r", "requirements.txt"})
  end,
  
  config = function()
    require("tempura").setup()
  end
}
```

## 🛠️ Usage

1. Scrape a Recipe

Use the :TempuraScrape command followed by the URL of the recipe you want to download.

:TempuraScrape [https://www.allrecipes.com/recipe/20263/tasty-salmon-patties/](https://www.allrecipes.com/recipe/20263/tasty-salmon-patties/)


This will:

Scrape the data.

Create a new buffer with the recipe in Markdown format.

Save the file to your Neovim data directory (~/.local/share/nvim/tempura_recipes/).

2. Convert Units

While in the recipe buffer (that you just scraped), you can convert the ingredient units.

Convert to Metric:

`:TempuraConvert metric`


Convert to Imperial:

`:TempuraConvert imperial`


The plugin will automatically locate the `## Ingredients 🧂` section and modify the quantities in place.