import networkx as nx
from typing import Any, Dict, List
from app.models.schemas import MatrixRequest

class GraphBuilder:
    """Service to construct NetworkX graphs from OSRM table data."""

    @staticmethod
    def build_from_matrix(matrix_data: Dict[str, Any], request: MatrixRequest) -> Dict[str, Any]:
        """
        Converts OSRM table response to a directed NetworkX graph.
        
        Nodes are indexed by their position in the request coordinates.
        Edges carry duration and distance attributes.
        """
        G = nx.DiGraph()
        
        durations = matrix_data.get("durations", [])
        distances = matrix_data.get("distances", [])
        
        # Add nodes with coordinates as metadata
        for i, coord in enumerate(request.coordinates):
            G.add_node(i, lon=coord.longitude, lat=coord.latitude)
            
        # Add edges for all possible pairs
        for i in range(len(durations)):
            for j in range(len(durations[i])):
                if i == j:
                    continue
                
                G.add_edge(i, j, 
                           duration=durations[i][j], 
                           distance=distances[i][j])
        
        # Return in node-link format for easy JSON serialization
        return nx.node_link_data(G)
