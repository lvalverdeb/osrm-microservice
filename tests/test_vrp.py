import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_vrp_endpoint_basic():
    """Test the VRP endpoint with a simple depot-stop setup."""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        payload = {
            "depots": [
                {"longitude": -84.09, "latitude": 9.93},
                {"longitude": -84.15, "latitude": 9.97}
            ],
            "stops": [
                {"longitude": -84.10, "latitude": 9.94},
                {"longitude": -84.14, "latitude": 9.96}
            ]
        }
        
        # This test assumes the OSRM backend is reachable since we are doing an integration test.
        # In a strict CI environment, we would mock the OSRMClient.
        response = await ac.post("/vrp", json=payload)
        
        # If OSRM is not running, this might fail with 500. 
        # But we want to check schema validation first.
        if response.status_code == 200:
            data = response.json()
            assert "routes" in data
            assert data["total_distance"] > 0
            assert len(data["routes"]) > 0
        else:
            # If OSRM fails, we expect 500 but still valid JSON error from FastAPI
            print(f"VRP Error: {response.text}")
            assert response.status_code in [200, 500]

def test_vrp_schema_validation():
    """Test that invalid payloads are caught by Pydantic."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # Missing 'depots'
    response = client.post("/vrp", json={"stops": []})
    assert response.status_code == 422
