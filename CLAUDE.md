# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project is a Python-based GUI automation tool designed to automate a purchasing workflow. It uses image recognition to find and interact with UI elements on the screen.

The primary application logic is contained in `auto_clicker.py`.

## Code Architecture

- **Main Script (`auto_clicker.py`)**: This is the entry point for the automation.
  - It uses a multi-library approach for maximum compatibility:
    - **`pyautogui`**: Used for its robust image recognition (`locateCenterOnScreen`).
    - **`win32api` / `win32con`**: Used for the most critical mouse clicking operations. This is the lowest-level method available in Python and is the most likely to be accepted by protected applications.
    - **`pydirectinput`**: Used for keyboard inputs (`write`, `hotkey`).
  - It contains the `ImageBasedAutomator` class, which encapsulates all core functionality.
  - **`ImageBasedAutomator` Class**:
    - `find_and_click()`: The core method. It locates an image with `pyautogui`, then performs a click using `win32api`.
    - `type_text()`: A method for inputting text via `pydirectinput`.
    - `run_purchase_workflow()`: Defines the sequence of operations for the entire purchasing process. **Modifications to the automation flow should be made here.**
  - The script is configured by modifying the variables within the `if __name__ == "__main__"` block (e.g., `TARGET_ITEM_KEYWORD`, `PURCHASE_QUANTITY`).

- **Image Assets (`images/` directory)**:
  - This directory is critical. It stores all the `.png` template images that `pyautogui` uses to find UI elements on the screen.
  - The naming convention for these images is `type_description.png` (e.g., `btn_buy.png`, `input_search.png`).
  - The quality and uniqueness of these images directly impact the success of the automation. Refer to `README.md` for detailed instructions on creating these assets.

- **Legacy Script (`click_logger.py`)**:
  - This script appears to be a previous implementation that relied on manually recording mouse coordinates (`pynput`). It generates the `key_mapping.json` file. It is likely deprecated in favor of the image-recognition approach in `auto_clicker.py`.

## Common Commands

### Setup and Installation

This project uses `uv` for dependency management. To set up the virtual environment and install all dependencies from `pyproject.toml`, run:
```bash
uv venv
uv sync
```

### Running the Application

To execute the main automation script, first activate the virtual environment.

**On Windows:**
```bash
.venv\\Scripts\\activate
```

**On macOS/Linux:**
```bash
source .venv/bin/activate
```

Then, run the script:
```bash
python auto_clicker.py
```
Before running, ensure the configuration variables at the bottom of `auto_clicker.py` are set correctly.
