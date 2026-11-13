from fastapi import APIRouter, HTTPException

from app.services import project_service as svc

router = APIRouter(tags=["demo"])


@router.post("/demo/{stream_id}")
def run_demo(stream_id: int):
    """Run the fixture wave-by-wave to show commit / reject / catch-up paths."""
    try:
        return svc.run_demo(stream_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
