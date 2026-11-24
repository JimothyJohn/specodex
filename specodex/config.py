import importlib
import inspect
import os
import pkgutil
from pathlib import Path
from typing import Dict, Type

from dotenv import load_dotenv

from specodex.models.product import ProductBase

# Load .env from project root
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

REGION: str = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME: str = os.environ.get("DYNAMODB_TABLE_NAME", "products")

PROMPT: str = """
Extract the products and their specifications from this technical catalog.
"""

GUARDRAILS: str = """
IMPORTANT EXTRACTION RULES:
- Extract EVERY specification that appears in the document for each product variant.
- If a value exists in the document, you MUST include it. Do NOT leave a cell empty when the data is present.
- Only leave a cell empty when the specification is genuinely absent from the document.
- Numeric cells must contain plain numbers only. The unit is already encoded in the column header — do not repeat it.
- If a spec is listed in a table, convert the exact numeric value into the unit shown in the column header.
- Pay close attention to footnotes, headers, and sub-tables that may contain additional specs.
- Each row or variant in a selection table is a separate product — emit one CSV row per variant.
"""

MODEL: str = "gemini-2.5-flash"  # Explicitly define model for clarity

# Pass to every genai.Client(...). google-genai >= 3.6 defaults to API v1,
# which rejects JSON mode (response_mime_type="application/json") with
# 400 INVALID_ARGUMENT; every extraction here uses JSON mode.
# tests/unit/test_gemini_api_version.py fails on any unpinned client.
GEMINI_HTTP_OPTIONS: dict = {"api_version": "v1beta"}


def _discover_schema_models() -> Dict[str, Type[ProductBase]]:
    """
    Automatically discover all Pydantic model schemas in the models directory.

    This function scans specodex/models/ for Python modules and imports
    any classes that inherit from BaseModel, using the module name as the key.

    Excludes base classes and utility modules (common, product).

    For example:
    - specodex/models/motor.py -> Motor class -> "motor": Motor
    - specodex/models/robot_arm.py -> RobotArm class -> "robot_arm": RobotArm

    Returns:
        Dict mapping model name (lowercase, underscored) to Pydantic model class

    Note: This eliminates the need to manually update SCHEMA_CHOICES when
          adding new product types. Just create a new model file and it will
          automatically be discovered.
    """
    schema_choices: Dict[str, Type[ProductBase]] = {}

    # Modules to skip (base classes, utilities)
    EXCLUDED_MODULES = {"common", "product", "__init__"}

    # Get the models directory path
    models_dir = Path(__file__).parent / "models"

    if not models_dir.exists():
        return schema_choices

    # Iterate through all Python modules in the models directory
    for module_info in pkgutil.iter_modules([str(models_dir)]):
        module_name = module_info.name

        # Skip __init__, private modules, and excluded base classes
        if module_name.startswith("_") or module_name in EXCLUDED_MODULES:
            continue

        try:
            # Import the module
            module = importlib.import_module(f"specodex.models.{module_name}")

            # Find all classes in the module that inherit from BaseModel
            for _, obj in inspect.getmembers(module, inspect.isclass):
                # Check if it's a Pydantic model (inherits from ProductBase)
                # and is defined in this module (not imported)
                if (
                    issubclass(obj, ProductBase)
                    and obj is not ProductBase
                    and obj.__module__ == module.__name__
                ):
                    # Use the module name as the key (e.g., "motor", "robot_arm")
                    schema_choices[module_name] = obj
                    # print(f"Discovered schema: {module_name} -> {obj.__name__}")
                    break  # Only take the first/main model from each module

        except (ImportError, AttributeError) as e:
            # Log but don't fail - some modules might not be valid
            print(f"Warning: Could not import model from {module_name}: {e}")
            continue

    return schema_choices


# Auto-discover all schema models from the models directory
# This replaces the manual dictionary definition
SCHEMA_CHOICES: Dict[str, Type[ProductBase]] = _discover_schema_models()
