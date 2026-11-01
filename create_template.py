#!/usr/bin/env python3
"""
Script pour créer le template Excel d'exemple.
"""

from excel_handler import create_template_excel
import os

if __name__ == "__main__":
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    os.makedirs(template_dir, exist_ok=True)

    template_path = os.path.join(template_dir, "template_input.xlsx")
    create_template_excel(template_path)

    print(f"Template créé: {template_path}")
