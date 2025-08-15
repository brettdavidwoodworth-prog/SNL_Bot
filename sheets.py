# sheets.py

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Setup the credentials and sheet
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("/home/brett_david_woodworth/SNL_Bot/creds.json", scope)
client = gspread.authorize(creds)

# Open the sheet (replace with your actual sheet name)
sheet = client.open("OSRS Events").sheet1  # Adjust if it's not the first worksheet

def get_tile_data(tile_number):
    # Load all rows from the sheet, starting from the 3rd row as headers per your note
    try:
        rows = sheet.get_all_records(head=3)
    except Exception as e:
        logger.error(f"Error loading rows in get_tile_data(): {e}")
        return None

    for row in rows:
        tile_str = str(row.get("Tile", "")).strip()
        if tile_str == "":
            # Skip empty tile cells (blank rows)
            continue
        try:
            tile = int(tile_str)
        except ValueError:
            print(f"Skipping invalid row in get_tile_data(): invalid literal for int() with base 10: '{tile_str}'")
            continue
        
        if tile == tile_number:
            # Safely parse End Tile, fallback to tile_number if missing or invalid
            try:
                end_tile = int(row.get("End Tile", tile))
            except (TypeError, ValueError):
                end_tile = tile
            
            tile_data = {
                "Tile": tile,
                "Target": row.get("Target", ""),
                "Task": row.get("Task", ""),
                "Drop Rate": row.get("Drop Rate", ""),
                "Type": row.get("Type", "").lower(),
                "End Tile": end_tile,
                "Image": row.get("Target Image", None),
            }
            return tile_data
    return None  # no matching tile found

def get_max_tile():
    """
    Return the highest tile number from the sheet.
    Assumes the third row contains headers.
    """
    try:
        records = sheet.get_all_records(head=3)
        tile_numbers = [int(row["Tile"]) for row in records if str(row["Tile"]).isdigit()]
        return max(tile_numbers) if tile_numbers else 100
    except Exception as e:
        logger.error(f"Could not fetch records in get_max_tile(): {e}")
        return 100
