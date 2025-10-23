local M = {}

-- Dynamically calculate the plugin's root path
local plugin_root = vim.fn.fnamemodify(debug.getinfo(1).source:sub(2), ':p:h:h:h')
local PY_SCRIPT_PATH = plugin_root .. '/python/tempura_cli.py'

-- Check Python version and availability
local function check_python_version()
    local version = vim.fn.system('python3 --version')
    if vim.v.shell_error ~= 0 then
        vim.notify("Tempura Error: Python3 is not installed or not in PATH", vim.log.levels.ERROR)
        return false
    end
    return true
end

--- Core Utility: Call Python Script ---
local function call_python_script(args)
    if not check_python_version() then
        return nil
    end

    if vim.fn.filereadable(PY_SCRIPT_PATH) == 0 then
        vim.notify("Tempura Error: Python script not found. Did the 'build' step fail?", vim.log.levels.ERROR)
        return nil
    end

    -- build a shell-escaped command string so we capture stderr too
    local parts = { vim.fn.shellescape('python3'), vim.fn.shellescape(PY_SCRIPT_PATH) }
    for _, arg in ipairs(args) do
        table.insert(parts, vim.fn.shellescape(tostring(arg)))
    end
    local cmd_str = table.concat(parts, ' ') .. ' 2>&1'

    local result = vim.fn.system(cmd_str)
    local status = vim.v.shell_error

    if status ~= 0 then
        -- show full python output (stdout+stderr) so we can debug conversion errors
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

    -- Basic URL validation
    if not url:match('^https?://') then
        vim.notify("Invalid URL format. URL must start with http:// or https://", vim.log.levels.WARN)
        return
    end

    vim.notify("Scraping recipe from " .. url .. "...", vim.log.levels.INFO)
    
    local markdown_content = call_python_script({'scrape', url})

    if markdown_content then
        -- Prefer the recipe title from the generated markdown as the filename
        local function sanitize_filename(name)
            if not name then return "" end
            name = name:lower()
            name = name:gsub("^%s+", ""):gsub("%s+$", "")
            -- remove fragment/hash and other problematic chars
            name = name:gsub("[#%%:;%?%&%=%./\\]+", "")
            -- keep only alphanumeric, hyphen and spaces
            name = name:gsub("[^%w%-%s]", "")
            -- replace spaces with dash
            name = name:gsub("%s+", "-")
            -- trim to reasonable length
            if name == "" or #name > 200 then
                return ""
            end
            return name
        end

        -- try to extract title from markdown (first H1)
        local title = markdown_content:match("^#%s*(.-)%s*\r?\n")
        local filename = sanitize_filename(title)

        -- fallback to last path segment of URL if no title
        if filename == "" then
            filename = url:match("([^/]+)$") or "new_recipe"
            -- remove query/fragment chars and replace slashes/dots
            filename = filename:gsub("[#%?%&%=%./\\]+", "_")
            filename = filename:gsub("^_+", ""):gsub("_+$", "")
            if filename == "" then
                filename = tostring(os.time()) .. '_recipe'
            end
            -- keep length reasonable
            if #filename > 200 then
                filename = filename:sub(1,200)
            end
        end

        local clean_filename = filename:gsub('^%s*(.-)%s*$', '%1') 
        
        -- Ensure filename is not empty and has a reasonable length
        if clean_filename == '' or #clean_filename > 255 then
            clean_filename = tostring(os.time()) .. '_recipe'
        end

        -- Save into the user's home dir: ~/.tempura-recipies
        local save_dir = vim.fn.expand('~/.tempura-recipies')

        -- Safe directory creation
        local ok_mkdir, mkdir_err = pcall(vim.fn.mkdir, save_dir, 'p')
        if not ok_mkdir then
            vim.notify("Failed to create directory: " .. tostring(mkdir_err), vim.log.levels.ERROR)
            return
        end

        local save_path = save_dir .. '/' .. clean_filename .. '.md'

        -- Safe file writing
        local ok, err = pcall(function()
            vim.cmd('edit ' .. vim.fn.fnameescape(save_path))
            vim.api.nvim_buf_set_lines(0, 0, -1, false, vim.split(markdown_content, '\n', {}))
        end)
        
        if not ok then
            vim.notify("Failed to save recipe: " .. tostring(err), vim.log.levels.ERROR)
            return
        end

        vim.notify("Recipe saved and opened: " .. save_path, vim.log.levels.INFO, { title = "Tempura.nvim" })
    end
end

--- Command Function: Convert Units ---
function M.convert(target_system)
    -- Ensure we're in a markdown file
    if vim.bo.filetype ~= 'markdown' then
        vim.notify("This command only works in markdown files.", vim.log.levels.WARN)
        return
    end

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

    -- Safe JSON encoding/decoding
    local ok, ingredients_json = pcall(vim.json.encode, ingredients)
    if not ok then
        vim.notify("Failed to process ingredients list.", vim.log.levels.ERROR)
        return
    end
    
    local result = call_python_script({'convert', system, ingredients_json})
    
    if result then
        local ok, converted_ingredients = pcall(vim.json.decode, result)
        if not ok or type(converted_ingredients) ~= "table" then
            vim.notify("Failed to parse conversion results.", vim.log.levels.ERROR)
            return
        end

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

-- Add these at the top with other local declarations
local has_telescope, telescope = pcall(require, 'telescope')
local telescope_builtin = require('telescope.builtin')
local recipes_dir = vim.fn.expand('~/.tempura-recipies')

-- Replace the find_recipes function with this debug version
local function find_recipes()
    -- Check for telescope
    if not has_telescope then
        vim.notify("Telescope.nvim is required but not found", vim.log.levels.ERROR)
        return
    end

    -- Debug logging
    vim.notify("Checking recipes directory: " .. recipes_dir)

    -- Ensure recipes directory exists
    if vim.fn.isdirectory(recipes_dir) == 0 then
        vim.notify("No recipes directory found at: " .. recipes_dir, vim.log.levels.WARN)
        -- Try to create it
        local ok = vim.fn.mkdir(recipes_dir, 'p')
        if ok == 0 then
            vim.notify("Failed to create recipes directory", vim.log.levels.ERROR)
            return
        end
    end

    -- Check for markdown files
    local handle = vim.loop.fs_scandir(recipes_dir)
    local has_recipes = false
    if handle then
        local name, type = vim.loop.fs_scandir_next(handle)
        while name do
            if name:match("%.md$") then
                has_recipes = true
                break
            end
            name, type = vim.loop.fs_scandir_next(handle)
        end
    end

    if not has_recipes then
        vim.notify("No recipe files found. Try scraping some recipes first!", vim.log.levels.WARN)
        return
    end

    -- Configure telescope
    local telescope_opts = {
        prompt_title = "📖 Tempura Recipes",
        cwd = recipes_dir,
        file_ignore_patterns = {"^%.", "^node_modules/"},
        find_command = {"find", recipes_dir, "-type", "f", "-name", "*.md"},
        layout_strategy = "horizontal",
        layout_config = {
            preview_width = 0.6,
            width = 0.9,
            height = 0.9,
        },
        path_display = {"smart"},
        previewer = require('telescope.previewers').vim_buffer_cat.new({
            file_encoding = "utf8",
        }),
    }

    -- Try to open telescope
    local ok, err = pcall(function()
        telescope_builtin.find_files(telescope_opts)
    end)

    if not ok then
        vim.notify("Failed to open recipe browser: " .. tostring(err), vim.log.levels.ERROR)
    end
end

--- Plugin Setup: Register Commands ---
function M.setup(opts)
    if type(opts) ~= "table" and opts ~= nil then
        vim.notify("Tempura setup options must be a table", vim.log.levels.ERROR)
        return
    end
    
    opts = opts or {}
    
    -- Check Python dependencies on setup
    if not check_python_version() then
        vim.notify("Tempura: Python3 is required but not found", vim.log.levels.ERROR)
        return
    end
    
    if vim.fn.filereadable(PY_SCRIPT_PATH) == 0 then
        vim.notify("Tempura: Python script not found at " .. PY_SCRIPT_PATH, vim.log.levels.ERROR)
        return
    end
    
    vim.api.nvim_create_user_command('TempuraScrape', function(cmd_opts)
        M.scrape(cmd_opts.fargs[1])
    end, { nargs = 1, desc = 'Scrape a recipe URL and save as Markdown' })

    vim.api.nvim_create_user_command('TempuraConvert', function(cmd_opts)
        M.convert(cmd_opts.fargs[1])
    end, {
        nargs = 1,
        complete = function()
            return { 'metric', 'imperial' }
        end,
        desc = 'Convert recipe units (metric/imperial)'
    })

    -- Add the new recipe finder command
    vim.api.nvim_create_user_command('TempuraFind', function()
        find_recipes()
    end, { desc = 'Find and browse saved recipes' })

    return M
end

return M
