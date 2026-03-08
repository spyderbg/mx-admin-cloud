from fastapi import Depends
from src.security.auth import verify_token

def get_current_user(username: str = Depends(verify_token)):
    return username
