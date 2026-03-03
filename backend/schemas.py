from pydantic import BaseModel


class SignupRequest(BaseModel):
    badge_number: str
    first_name: str
    last_name: str
    team_number: str
    phone_number: str | None = None
    password: str


class LoginRequest(BaseModel):
    badge_number: str
    password: str


class OccurrenceReport(BaseModel):
    call_number: str
    vehicle_id: str
    classification: str
    occurrence_type: str
    brief_description: str


class UpdateDraftRequest(BaseModel):
    patch: dict


class AgentChatRequest(BaseModel):
    message: str
