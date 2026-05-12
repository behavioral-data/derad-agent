import os


PATH_SECRETS = "secrets"


def get_secret(filename: str) -> str:
    """
    Helper Function to retrieve secret from files
    """
    path_key = str(PATH_SECRETS / f"{filename}.txt")

    if os.path.exists(path_key):
        with open(path_key, encoding="utf-8") as f:
            key = f.read().strip()
        return key

    raise ValueError(f"Secret not available at: {path_key}")
