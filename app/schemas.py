from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class UserPreferences(BaseModel):
    allergies: List[str] = []
    dislikes: List[str] = []
    goal: str = "none"  # e.g., diet, bulk, maintain, none

class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    preferences: Optional[UserPreferences] = None

class UserResponse(BaseModel):
    uid: str
    email: str
    display_name: Optional[str] = None
    preferences: Optional[UserPreferences] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
