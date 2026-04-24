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

### `NearestRequest` (Hérite de `CommonRoutingOptions`)

| Champ | Type | Description |
| :--- | :--- | :--- |
| `coordinate` | `Coordinate` | Point à aligner sur le réseau. |
| `number` | `int` | Nombre de segments les plus proches à retourner (Par défaut : 1). |
| `profile` | `str` | Profil de routage : `driving`, `cycling`, `walking`. |

---

## Points de Terminaison (Endpoints)

### 1. Itinéraire (Routing)

#### `POST /route`

Calcule l'itinéraire le plus rapide entre deux points.

**Exemple de Requête (`RouteRequest`) :**

```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "profile": "walking",
  "steps": true
}
```

---

### 5. Nearest (Alignement Routier)

#### `POST /nearest`

Trouve le ou les points du réseau routier les plus proches d'une coordonnée donnée.

**Exemple de Requête (`NearestRequest`) :**

```json
{
  "coordinate": {"longitude": -84.09, "latitude": 9.93},
  "number": 3,
  "profile": "cycling"
}
```

---

### 6. Tuiles (Mapbox Vector Tiles)

#### `GET /tile/{profile}/{z}/{x}/{y}.mvt`

Proxy d'une tuile vectorielle Mapbox depuis OSRM. Niveau de zoom minimum : 12.

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
