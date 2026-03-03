from pydantic import BaseModel


class OccurrenceReport(BaseModel):
    call_number: str
    vehicle_id: str
    classification: str
    occurrence_type: str
    brief_description: str


class UpdateDraftRequest(BaseModel):
    badge_number: str
    patch: dict


