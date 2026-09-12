"""Constants"""

# Ninebot scooter BLE manufacturer ids seen in the wild:
# 0x424E (16974) on older generations ("NB..." style names), 0x434E (17230)
# on newer generations that advertise a serial-style name.
NINEBOT_MANUFACTURER_IDS = (0x424E, 0x434E)

# Backwards-compatible alias for the classic id.
NINEBOT_MANUFACTURER_ID = 0x424E
