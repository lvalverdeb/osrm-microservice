# Référence API - Microservice Backend OSRM

Ce document fournit une référence détaillée pour les développeurs interagissant avec le microservice Backend OSRM.

## URL de Base

Le service s'exécute par défaut sur le port `8000` (mappé au `8080` dans Docker).

- **Local**: `http://localhost:8000`
- **Docker**: `http://localhost:8080`

---

## Modèles de Données (Schémas)

Les modèles Pydantic suivants définissent la structure des requêtes et des réponses.

### `Coordinate`

Représentation standard d'un point géographique.

| Champ | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude du point en degrés décimaux. |
| `latitude` | `float` | Latitude du point en degrés décimaux. |

### `CommonRoutingOptions`

Options générales OSRM facultatives applicables aux services Route, Table, Match et Trip.

| Champ | Type | Description |
| :--- | :--- | :--- |
| `bearings` | `List[str]` | Contraintes d'orientation par coordonnée (ex: '90,30'). |
| `radiuses` | `List[float]` | Rayon d'ajustement par coordonnée en mètres. Utilisez `null` pour illimité. |
| `hints` | `List[str]` | Chaînes d'indices provenant d'une réponse OSRM précédente. |
| `approaches` | `List[str]` | Côté d'approche par coordonnée : `unrestricted` ou `curb`. |
| `exclude` | `List[str]` | Classes de routes à exclure globalement (ex: `['motorway', 'toll']`). |
| `snapping` | `str` | Sélection des segments : `default` ou `any`. |
| `skip_waypoints` | `bool` | Supprimer le tableau des waypoints dans la réponse. |

### `RouteRequest` (Hérite de `CommonRoutingOptions`)

| Champ | Type | Description |
| :--- | :--- | :--- |
| `origin` | `Coordinate` | Point de départ de l'itinéraire. |
| `destination` | `Coordinate` | Point de destination final. |
| `waypoints` | `List[Coordinate]` | Points intermédiaires facultatifs. |
| `profile` | `str` | Profil de routage : `driving` (par défaut), `cycling`, `walking`. |
| `alternatives` | `bool ou int` | Retourner des alternatives (booléen) ou un nombre spécifique (entier). |
| `overview` | `str` | Résolution de la géométrie : `simplified` (par défaut), `full`, `false`. |
| `geometries` | `str` | Format de la géométrie : `polyline` (par défaut), `polyline6`, `geojson`. |
| `steps` | `bool` | Retourner les instructions de virage (Par défaut : `true`). |
| `annotations` | `str` | Métadonnées par segment (ex: `distance,duration`). |

### `MatrixRequest` (Hérite de `CommonRoutingOptions`)

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Liste de points à inclure dans le calcul. |
| `profile` | `str` | Profil de routage : `driving`, `cycling`, `walking`. |
| `sources` | `List[int]` | Indices des points à utiliser comme origines. |
| `destinations` | `List[int]` | Indices des points à utiliser comme destinations. |
| `annotations` | `str` | `duration` (par défaut), `distance`, ou `duration,distance`. |

### `MatchRequest` (Hérite de `CommonRoutingOptions`)

| Champ | Type | Description |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Séquence de points pour s'aligner sur le réseau routier. |
| `profile` | `str` | Profil de routage : `driving`, `cycling`, `walking`. |
| `overview` | `str` | Résolution de la géométrie : `simplified`, `full`, `false`. |
| `geometries` | `str` | Format de la géométrie : `polyline`, `polyline6`, `geojson`. |
| `steps` | `bool` | Retourner les étapes de l'itinéraire ajusté. |
| `annotations` | `str` | Métadonnées par segment séparées par des virgules. |
| `gaps` | `str` | Scinder la trace sur des écarts importants : `split` ou `ignore`. |
| `tidy` | `bool` | Supprimer les coordonnées répétées ou désordonnées avant l'ajustement. |
| `match_waypoints` | `List[int]` | Indices des breadcrumbs à traiter comme des waypoints explicites. |

### `GPSBreadcrumb`

Un point de tracé GPS individ.

| Champ | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude du point. |
| `latitude` | `float` | Latitude du point. |
| `timestamp` | `int` | Horodatage Unix. |
| `accuracy_meters` | `float` | Rayon d'ajustement / précision en mètres (Par défaut : `5.0`). |

### `Stop` (Hérite de `Coordinate`)

Un point de livraison géographique ou l'emplacement d'un dépôt avec identification.

| Champ | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude du point. |
| `latitude` | `float` | Latitude du point. |
| `id` | `str ou int` | Identifiant unique facultatif pour le suivi. |

### `TripRequest` (Hérite de `CommonRoutingOptions`)

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Coordonnées à optimiser. |
| `roundtrip` | `bool` | Revenir au premier point à la fin (Par défaut : `true`). |
| `source` | `str` | Restriction de départ : `first` ou `any`. |
| `destination` | `str` | Restriction d'arrivée : `last` ou `any`. |
| `profile` | `str` | Profil de routage : `driving`, `cycling`, `walking`. |
| `overview` | `str` | Résolution de la géométrie : `simplified`, `full`, `false`. |
| `geometries` | `str` | Format de la géométrie : `polyline`, `polyline6`, `geojson`. |
| `steps` | `bool` | Retourner les instructions étape par étape. |
| `annotations` | `str` | Métadonnées de segment séparées par des virgules. |

### `NearestRequest` (Hérite de `CommonRoutingOptions`)

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinate` | `Coordinate` | Point à aligner sur le réseau. |
| `number` | `int` | Nombre de segments les plus proches à retourner (Par défaut : 1). |
| `profile` | `str` | Profil de routage : `driving`, `cycling`, `walking`. |

### `NearestResponse`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `code` | `str` | Code d'état de l'opération (ex : `Ok`). |
| `waypoints` | `List[Dict]` | Métadonnées des segments routiers les plus proches. |

### `VrpRequest`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `depots` | `List[Stop]` | Liste des entrepôts / dépôts. |
| `stops` | `List[Stop]` | Liste des points de livraison. |
| `vehicle_count` | `int` | Nombre de véhicules disponibles. Par défaut, un par dépôt. |
| `capacity` | `int` | Capacité maximale de livraison par véhicule (Par défaut : 35). |
| `max_radius_km` | `float` | Distance maximale par route depuis le dépôt (km) facultative. |
| `clustering_mode` | `str` | Type de regroupement : `travel_time` (par défaut), `distance` ou `radial`. |
| `hysteresis_m` | `float` | Tolérance de limite de dépôt en mètres (Par défaut : `2000.0`). |
| `roundtrip` | `bool` | Revenir au dépôt à la fin de la tournée (Par défaut : `true`). |

### `VehicleRoute`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `vehicle_id` | `str ou int` | Identifiant du véhicule (avec suffixe). |
| `depot_index` | `int` | Indice de l'entrepôt assigné. |
| `stops_indices` | `List[int]` | Séquence optimisée des indices de livraison. |
| `stop_ids` | `List[str ou int]` | Liste facultative d'identifiants de livraison dans l'ordre optimisé. |
| `stop_coordinates` | `List[Coordinate]` | Coordonnées dans l'ordre optimisé. |
| `route_geometry` | `Dict` | Géométrie GeoJSON LineString de l'itinéraire. |
| `distance_meters` | `float` | Distance totale en mètres. |
| `duration_seconds` | `float` | Durée totale en secondes. |

### `VrpResponse`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `code` | `str` | Code d'état de la réponse. |
| `routes` | `List[VehicleRoute]` | Itinéraires optimisés par véhicule. |
| `total_distance` | `float` | Distance totale de toute la flotte. |
| `total_duration` | `float` | Durée totale de voyage de toute la flotte. |

### `VrpAllocationResponse`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `code` | `str` | Code d'état de la réponse. |
| `allocations` | `Dict[str/int, List]` | Identifiants de dépôt associés aux arrêts assignés. |
| `unreachable_stops` | `List` | Liste des identifiants/indices de livraison hors limites. |

---

## Points de Terminaison (Endpoints)

### Endpoints Système

#### `GET /health`

Vérifie si la passerelle est en cours d'exécution.

**Corps de la Réponse :**
```json
{
  "status": "healthy",
  "service": "osrm-api-gateway"
}
```

---

### Endpoints d'Itinéraire (Routing)

#### `POST /route`

Calcule l'itinéraire le plus rapide entre deux points.

**Corps de la Requête (`RouteRequest`) :**
```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "profile": "walking",
  "steps": true
}
```

**Corps de la Réponse (JSON) :** Renvoie directement la réponse du service `/route` d'OSRM contenant `code`, `routes` et `waypoints`.

---

#### `POST /nearest`

Aligne une coordonnée sur les segments routiers les plus proches.

**Corps de la Requête (`NearestRequest`) :**
```json
{
  "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
  "number": 1,
  "profile": "driving"
}
```

**Corps de la Réponse (`NearestResponse`) :**
```json
{
  "code": "Ok",
  "waypoints": [
    {
      "name": "Calle Central",
      "distance": 4.2,
      "location": [-84.0906, 9.9282],
      "hint": "abc..."
    }
  ]
}
```

---

### Endpoints de Matrice

#### `POST /matrix`

Calcule les durées et distances de voyage entre toutes les localisations fournies.

**Corps de la Requête (`MatrixRequest`) :**
```json
{
  "coordinates": [
    {"longitude": -84.0907, "latitude": 9.9281},
    {"longitude": -84.0833, "latitude": 9.9333}
  ],
  "profile": "driving"
}
```

**Corps de la Réponse :** Renvoie directement la réponse du service `/table` d'OSRM contenant `code`, `durations`, `distances`, `sources` et `destinations`.

---

#### `POST /matrix-graph`

Génère une représentation sous forme de graphe orienté sérialisable de la matrice.

**Corps de la Requête (`MatrixRequest`) :** Identique à `POST /matrix`.

**Corps de la Réponse (`MatrixGraphResponse`) :**
```json
{
  "nodes": [{"id": 0, "lon": -84.0907, "lat": 9.9281}],
  "edges": [{"source": 0, "target": 1, "duration": 180.0, "distance": 1200.0}]
}
```

---

### Endpoints d'Appariement de Cartes (Map Matching)

#### `POST /match`

Aligne des traces GPS parasitées sur le réseau routier.

**Corps de la Requête (`MatchRequest`) :**
```json
{
  "breadcrumbs": [
    {"longitude": -84.0907, "latitude": 9.9281, "timestamp": 1713000000},
    {"longitude": -84.0880, "latitude": 9.9300, "timestamp": 1713000030}
  ],
  "profile": "driving",
  "tidy": true
}
```

**Corps de la Réponse :** Renvoie directement la réponse du service `/match` d'OSRM contenant `code`, `matchings` et `tracepoints`.

---

### Endpoints d'Optimisation

#### `POST /trip`

Optimise une séquence de visites (Problème du Voyageur de Commerce - TSP).

**Corps de la Requête (`TripRequest`) :**
```json
{
  "coordinates": [
    {"longitude": -84.0907, "latitude": 9.9281},
    {"longitude": -84.0833, "latitude": 9.9333},
    {"longitude": -84.1000, "latitude": 9.9400}
  ],
  "roundtrip": true,
  "profile": "driving"
}
```

**Corps de la Réponse :** Renvoie directement la réponse du service `/trip` d'OSRM contenant `code`, `trips` et `waypoints`.

---

#### `POST /vrp`

Résout des problèmes de tournées de véhicules (VRP) multi-véhicules en utilisant le regroupement Localisation-Attribution.

**Corps de la Requête (`VrpRequest`) :**
```json
{
  "depots": [{"id": "D1", "longitude": -84.09, "latitude": 9.93}],
  "stops": [
    {"id": "S1", "longitude": -84.10, "latitude": 9.94},
    {"id": "S2", "longitude": -84.14, "latitude": 9.96}
  ],
  "vehicle_count": 2,
  "capacity": 35
}
```

**Corps de la Réponse (`VrpResponse`) :**
```json
{
  "code": "Ok",
  "routes": [
    {
      "vehicle_id": "D1-1",
      "depot_index": 0,
      "stops_indices": [0, 1],
      "stop_ids": ["S1", "S2"],
      "stop_coordinates": [
        {"longitude": -84.10, "latitude": 9.94},
        {"longitude": -84.14, "latitude": 9.96}
      ],
      "route_geometry": {
        "type": "LineString",
        "coordinates": [[-84.09, 9.93], [-84.10, 9.94], [-84.14, 9.96], [-84.09, 9.93]]
      },
      "distance_meters": 12450.0,
      "duration_seconds": 920.0
    }
  ],
  "total_distance": 12450.0,
  "total_duration": 920.0
}
```

---

#### `POST /vrp/allocate`

Pré-regroupe les livraisons par dépôts avant le routage (idéal pour vérifier les affectations).

**Corps de la Requête (`VrpRequest`) :** Identique à `POST /vrp`.

**Corps de la Réponse (`VrpAllocationResponse`) :**
```json
{
  "code": "Ok",
  "allocations": {
    "D1": ["S1", "S2"]
  },
  "unreachable_stops": []
}
```

---

### Endpoints des Tuiles (Tiles)

#### `GET /tile/{profile}/{z}/{x}/{y}.mvt`

Proxy des tuiles vectorielles Mapbox du backend de OSRM. Niveau de zoom minimum : 12.

---

## Gestion des Erreurs

Le service renvoie des corps d'erreur structurés d'OSRM lorsqu'ils sont disponibles :

```json
{
  "detail": {
    "code": "NoRoute",
    "message": "Could not find a route between coordinates"
  }
}
```
