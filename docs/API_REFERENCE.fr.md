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

### `GPSBreadcrumb`

Représente un point unique dans une trace GPS pour le map matching.

| Champ | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude en degrés décimaux. |
| `latitude` | `float` | Latitude en degrés décimaux. |
| `timestamp` | `int` | Horodatage Unix (secondes entières). |
| `accuracy_meters` | `float` | Précision en mètres (Par défaut : 5.0). |

### `RouteRequest`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `origin` | `Coordinate` | Point de départ de l'itinéraire. |
| `destination` | `Coordinate` | Point de destination final. |
| `waypoints` | `List[Coordinate]` | Points intermédiaires facultatifs à traverser. |
| `alternatives` | `bool ou int` | S'il faut retourner des itinéraires alternatifs (booléen) ou un nombre spécifique (entier). (Par défaut : `false`). |

### `TripRequest`

Utilisé pour résoudre le Problème du Voyageur de Commerce (TSP).

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Liste de points à optimiser. |
| `roundtrip` | `bool` | Si le voyage retourne au départ (Par défaut : `true`). |
| `source` | `str` | Exigence du point de départ (ex: `first`, `any`). (Par défaut : `first`). |
| `destination` | `str` | Exigence du point d'arrivée (ex: `last`, `any`). (Par défaut : `last`). |

### `MatchRequest`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Séquence de points à aligner sur le réseau routier. |

### `MatrixRequest`

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Liste de points à inclure dans le calcul. |
| `sources` | `List[int]` | Indices des points à utiliser comme origines. |
| `destinations` | `List[int]` | Indices des points à utiliser comme destinations. |

---

## Points de Terminaison (Endpoints)

### 1. Itinéraire (Routing)

#### `POST /route`

Calcule l'itinéraire routier le plus rapide entre un point d'origine et de destination, avec des points intermédiaires optionnels et des itinéraires alternatifs.

**Exemple de Requête :**

```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "waypoints": [],
  "alternatives": true
}
```

**Exemple de Réponse :**

```json
{
  "code": "Ok",
  "routes": [
    {
      "geometry": {
        "type": "LineString",
        "coordinates": [[-84.09, 9.93], [-84.15, 9.97]]
      },
      "legs": [
        {
          "steps": [],
          "distance": 12500.5,
          "duration": 945.2,
          "summary": "Résumé de l'itinéraire",
          "weight": 945.2
        }
      ],
      "distance": 12500.5,
      "duration": 945.2,
      "weight_name": "routability",
      "weight": 945.2
    }
  ],
  "waypoints": [
    {"name": "Origine", "location": [-84.09, 9.93]},
    {"name": "Destination", "location": [-84.15, 9.97]}
  ]
}
```

---

### 2. Matrice (Tables de Distance)

#### `POST /matrix`

Génère un tableau des durées et distances entre toutes les coordonnées fournies.

**Corps de la Requête (`MatrixRequest`) :**

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Liste des points à inclure dans la matrice. |
| `sources` | `Optional[List[int]]` | Indices dans la liste des coordonnées à utiliser comme origines. |
| `destinations` | `Optional[List[int]]` | Indices dans la liste des coordonnées à utiliser comme destinations. |

**Exemple :**

```json
{
  "coordinates": [
    {"longitude": -84.09, "latitude": 9.93},
    {"longitude": -84.15, "latitude": 9.97}
  ],
  "sources": [0],
  "destinations": [1]
}
```

**Exemple de Réponse :**

```json
{
  "code": "Ok",
  "durations": [[0, 945.2], [940.1, 0]],
  "distances": [[0, 12500.5], [12450.2, 0]],
  "sources": [{"location": [-84.09, 9.93]}],
  "destinations": [{"location": [-84.15, 9.97]}]
}
```

#### `POST /matrix-graph`

Génère une matrice de distance/durée et la convertit en un format de graphe sérialisable (Nœuds et Arêtes) compatible avec NetworkX et d'autres bibliothèques de graphes.

**Corps de la Requête (`MatrixRequest`) :**
Identique à `POST /matrix`.

**Exemple de Réponse :**

```json
{
  "nodes": [
    {"id": 0, "longitude": -84.09, "latitude": 9.93},
    {"id": 1, "longitude": -84.15, "latitude": 9.97}
  ],
  "edges": [
    {"source": 0, "target": 1, "distance": 12500.5, "duration": 945.2},
    {"source": 1, "target": 0, "distance": 12450.2, "duration": 940.1}
  ]
}
```

---

### 3. Appariement de Cartes (Map Matching)

#### `POST /match`

Aligne les traces GPS bruitées sur le réseau routier. Gère automatiquement la division des traces en cas de perte de signal.

**Exemple de Requête :**

```json
{
  "breadcrumbs": [
    {"longitude": -84.09, "latitude": 9.93, "timestamp": 1741185000},
    {"longitude": -84.091, "latitude": 9.931, "timestamp": 1741185010}
  ]
}
```

**Exemple de Réponse :**

```json
{
  "code": "Ok",
  "matchings": [
    {
      "confidence": 0.95,
      "geometry": {
        "type": "LineString",
        "coordinates": [[-84.09, 9.93], [-84.091, 9.931]]
      },
      "distance": 150.2,
      "duration": 12.5
    }
  ],
  "tracepoints": [
    {"matchings_index": 0, "waypoint_index": 0, "location": [-84.09, 9.93]},
    {"matchings_index": 0, "waypoint_index": 1, "location": [-84.091, 9.931]}
  ]
}
```

---

### 4. Optimisation (TSP)

#### `POST /trip`

Résout le Problème du Voyageur de Commerce pour trouver la séquence la plus efficace pour visiter plusieurs coordonnées.

**Corps de la Requête (`TripRequest`) :**

```json
{
  "coordinates": [
    {"longitude": -84.09, "latitude": 9.93},
    {"longitude": -84.05, "latitude": 9.93},
    {"longitude": -84.07, "latitude": 9.91}
  ],
  "roundtrip": true,
  "source": "first",
  "destination": "any"
}
```

**Exemple de Réponse :**

Renvoie une géométrie GeoJSON et la séquence optimisée dans `waypoints[].waypoint_index`.

```json
{
  "code": "Ok",
  "trips": [
    {
      "geometry": { "type": "LineString", "coordinates": [...] },
      "distance": 8500.2,
      "duration": 620.5
    }
  ],
  "waypoints": [
    { "waypoint_index": 0, "location": [-84.09, 9.93], "name": "Départ" },
    { "waypoint_index": 2, "location": [-84.05, 9.93], "name": "Arrêt 2" },
    { "waypoint_index": 1, "location": [-84.07, 9.91], "name": "Arrêt 1" }
  ]
}
```

---

### 5. Système

#### `GET /health`

Renvoie l'état du service et les métadonnées.

**Réponse :**

```json
{
  "status": "healthy",
  "service": "osrm-microservice"
}
```

---

## Gestion des Erreurs

Tous les endpoints renvoient des codes d'erreur HTTP standard :

- **422 Unprocessable Entity** : Schéma de requête invalide (validation Pydantic).
- **500 Internal Server Error** : Échec de l'API OSRM en aval ou erreur de logique interne.
