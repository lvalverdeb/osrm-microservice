from app.models.schemas import Coordinate, MatrixRequest
from app.services.graph_builder import GraphBuilder


def test_build_from_matrix_empty():
    matrix_data = {}
    request = MatrixRequest(
        coordinates=[
            Coordinate(longitude=-84.09, latitude=9.93),
            Coordinate(longitude=-84.08, latitude=9.94),
        ]
    )
    result = GraphBuilder.build_from_matrix(matrix_data, request)
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 0


def test_build_from_matrix_basic():
    matrix_data = {
        "durations": [[0, 120, 240], [120, 0, 180], [240, 180, 0]],
        "distances": [[0, 5000, 10000], [5000, 0, 8000], [10000, 8000, 0]]
    }
    request = MatrixRequest(
        coordinates=[
            Coordinate(longitude=-84.09, latitude=9.93),
            Coordinate(longitude=-84.08, latitude=9.94),
            Coordinate(longitude=-84.10, latitude=9.92),
        ]
    )
    result = GraphBuilder.build_from_matrix(matrix_data, request)
    assert len(result["nodes"]) == 3
    assert len(result["edges"]) == 6
    assert result["nodes"][0]["lon"] == -84.09
    assert result["nodes"][0]["lat"] == 9.93
    assert result["nodes"][1]["lon"] == -84.08


def test_build_from_matrix_missing_distances():
    matrix_data = {
        "durations": [[0, 120], [120, 0]],
    }
    request = MatrixRequest(
        coordinates=[
            Coordinate(longitude=-84.09, latitude=9.93),
            Coordinate(longitude=-84.08, latitude=9.94),
        ]
    )
    result = GraphBuilder.build_from_matrix(matrix_data, request)
    assert len(result["edges"]) == 2
    for edge in result["edges"]:
        assert "duration" in edge


def test_build_from_matrix_coordinate_metadata():
    matrix_data = {
        "durations": [[0, 60], [60, 0]],
        "distances": [[0, 3000], [3000, 0]]
    }
    request = MatrixRequest(
        coordinates=[
            Coordinate(longitude=-84.09, latitude=9.93),
            Coordinate(longitude=-84.08, latitude=9.94),
        ]
    )
    result = GraphBuilder.build_from_matrix(matrix_data, request)
    for node in result["nodes"]:
        assert "lon" in node
        assert "lat" in node
