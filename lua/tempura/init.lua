local M = {}

-- Dynamically calculate the plugin's root path by navigating up from this file's location.
local plugin_root = vim.fn.fnamemodify(debug.getinfo(1).source:sub(2), ':p:h:h:h')
local PY_SCRIPT_PATH = plugin_root .. '/python/tempura_cli.py'

--- Core Utility: Call Python Script ---
local function call_python_script(args)
    if vim.fn.filereadable(PY_SCRIPT_PATH) == 0 then
        vim.notify("Tempura Error: Python script not found. Did the 'build' step fail?", vim.log.levels.ERROR)
        return nil
    end

    local cmd = { 'python3', PY_SCRIPT_PATH }
    
    for _, arg in ipairs(args) do
        table.insert(cmd, arg)
    end

    local result = vim.fn.system(cmd)
    local status = vim.v.shell_error

    if status ~= 0 then
        vim.notify("Tempura Error: Python script failed. Output: " .. result, vim.log.levels.ERROR, { title = "Tempura.nvim" })
        return nil
    end
    
    return result
end

--- Command Function: Scrape Recipe ---
function M.scrape(url)
    if not url or url == '' then
        vim.notify("Please provide a recipe URL.", vim.log.levels.WARN)
        return
    end

    vim.notify("Scraping recipe from " .. url .. "...", vim.log.levels.INFO)
    
    local markdown_content = call_python_script({'scrape', url})

    if markdown_content then
        local filename = url:match("([^/]+)$") or "new_recipe"
        local clean_filename = filename:gsub('[%?%&%=%./]', '_'):gsub('^%s*(.-)%s*$', '%1') 
        local save_dir = vim.fn.stdpath('data') .. '/tempura_recipes'
        vim.fn.mkdir(save_dir, 'p')
        local save_path = save_dir .. '/' .. clean_filename .. '.md'
        
        vim.cmd('edit ' .. save_path)
        vim.api.nvim_buf_set_lines(0, 0, -1, false, vim.split(markdown_content, '\n', {}))
        
        vim.notify("Recipe saved and opened: " .. save_path, vim.log.levels.INFO, { title = "Tempura.nvim" })
    end
end

--- Command Function: Convert Units ---
function M.convert(target_system)
    local system = target_system:lower()
    if system ~= 'metric' and system ~= 'imperial' then
        vim.notify("Invalid system. Use 'metric' or 'imperial'.", vim.log.levels.WARN)
        return
    end

    local lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
    local start_line, end_line, ingredients = nil, nil, {}
    local in_ingredients_section = false

    for i, line in ipairs(lines) do
        if line:match("^## Ingredients 🧂") then
            start_line = i + 1
            in_ingredients_section = true
        elseif in_ingredients_section and line:match("^## ") then
            end_line = i - 1
            break
        elseif in_ingredients_section and line:match("^%* ") then
            table.insert(ingredients, line:sub(3)) 
        end
    end

    if not start_line then
        vim.notify("Could not find '## Ingredients 🧂' section. Ensure the recipe is correctly formatted.", vim.log.levels.ERROR)
        return
    end
    
    if not end_line then
        end_line = #lines
    end

    vim.notify("Converting units to " .. system .. "...", vim.log.levels.INFO)

    local ingredients_json = vim.json.encode(ingredients)
    local result = call_python_script({'convert', system, ingredients_json})

    if result then
        local converted_ingredients = vim.json.decode(result)
        local new_lines = {}
        
        for _, ing in ipairs(converted_ingredients) do
            table.insert(new_lines, '* ' .. ing)
        end
        
        local start_index_0 = start_line - 1
        local end_index_0 = end_line
        
        vim.api.nvim_buf_set_lines(0, start_index_0, end_index_0, false, new_lines)
        
        vim.notify("Units converted successfully to " .. system .. "!", vim.log.levels.INFO, { title = "Tempura.nvim" })
    end
end

--- Plugin Setup: Register Commands ---
function M.setup(opts)
    opts = opts or {}
    
    vim.api.nvim_create_user_command('TempuraScrape', function(cmd_opts)
        M.scrape(cmd_opts.fargs[1])
    end, { nargs = 1, desc = 'Scrape a recipe URL and save as Markdown' })

    -- FIX: Changed 'complete' to a string of comma-separated options for simple completion.
    -- The previous version used a table, which is only valid if 'complete' is set to a function.
    vim.api.nvim_create_user_command('TempuraConvert', function(cmd_opts)
        M.convert(cmd_opts.fargs[1])
    end, { nargs = 1, complete = 'metric,imperial', desc = 'Convert recipe units (metric/imperial)' })
end

return M
