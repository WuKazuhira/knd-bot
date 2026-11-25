# Adapted from AiriCore plugins/airi_status (MIT License)

def truncate_string(string: str, length: int = 32):
    if len(string) > length:
        return string[: length - 3] + "..."
    return string
