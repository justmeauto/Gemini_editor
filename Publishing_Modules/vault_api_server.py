"""
Publishing_Modules/vault_api_server.py (Backward-Compatibility Shim)
=======================================================================
Re-exports everything from Telegram_Storage_Modules.vault_api_server.
"""
from Telegram_Storage_Modules.vault_api_server import *
from Telegram_Storage_Modules.vault_api_server import app

if __name__ == "__main__":
    import uvicorn
    import os
    host = os.getenv("VAULT_HOST", "0.0.0.0")
    port = int(os.getenv("VAULT_PORT", "8787"))
    uvicorn.run("Telegram_Storage_Modules.vault_api_server:app", host=host, port=port, reload=False)
