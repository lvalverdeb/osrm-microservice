# Referencia de la API - Microservicio Backend OSRM

Este documento proporciona una referencia detallada para los desarrolladores que interactúan con el microservicio Backend OSRM.

## URL Base

El servicio se ejecuta de forma predeterminada en el puerto `8000` (mapeado al `8080` en Docker).

- **Local**: `http://localhost:8000`
- **Docker**: `http://localhost:8080`

---

## Modelos de Datos (Esquemas)

Los siguientes modelos de Pydantic definen la estructura de las solicitudes y respuestas.

### `Coordinate`

Representación estándar de un punto geográfico.

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitud del punto en grados decimales. |
| `latitude` | `float` | Latitud del punto en grados decimales. |

### `GPSBreadcrumb`

Representa un único punto en un rastro de GPS para el emparejamiento de mapas (map matching).

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitud en grados decimales. |
| `latitude` | `float` | Latitud en grados decimales. |
| `timestamp` | `int` | Marca de tiempo Unix (segundos enteros). |
| `accuracy_meters` | `float` | Precisión en metros (Predeterminado: 5.0). |

### `RouteRequest`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `origin` | `Coordinate` | Punto de inicio de la ruta. |
| `destination` | `Coordinate` | Punto de destino final. |
| `waypoints` | `List[Coordinate]` | Puntos intermedios opcionales para pasar. |
| `alternatives` | `bool o int` | Si se deben devolver rutas alternativas (booleano) o un número específico (entero). (Por defecto: `false`). |

### `TripRequest`

Utilizado para resolver el Problema del Viajante (TSP).

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Lista de puntos a optimizar. |
| `roundtrip` | `bool` | Si el viaje regresa al inicio (Por defecto: `true`). |
| `source` | `str` | Requisito de punto de inicio (ej. `first`, `any`). (Por defecto: `first`). |
| `destination` | `str` | Requisito de punto final (ej. `last`, `any`). (Por defecto: `last`). |

### `MatchRequest`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Secuencia de puntos para ajustar a la red de carreteras. |

### `MatrixRequest`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Lista de puntos a incluir en el cálculo. |
| `sources` | `List[int]` | Índices de puntos para usar como orígenes. |
| `destinations` | `List[int]` | Índices de puntos para usar como destinos. |

---

## Endpoints

### 1. Enrutamiento (Routing)

#### `POST /route`

Calcula la ruta de conducción más rápida entre un origen y un destino, con puntos intermedios opcionales y rutas alternativas.

**Ejemplo de Solicitud:**

```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "waypoints": [],
  "alternatives": true
}
```

**Ejemplo de Respuesta:**

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
          "summary": "Resumen de la ruta",
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
    {"name": "Origen", "location": [-84.09, 9.93]},
    {"name": "Destino", "location": [-84.15, 9.97]}
  ]
}
```

---

### 2. Matriz (Tablas de Distancia)

#### `POST /matrix`

Genera una tabla de duraciones y distancias entre todas las coordenadas proporcionadas.

**Cuerpo de la Solicitud (`MatrixRequest`):**

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Lista de puntos a incluir en la matriz. |
| `sources` | `Optional[List[int]]` | Índices en la lista de coordenadas para usar como orígenes. |
| `destinations` | `Optional[List[int]]` | Índices en la lista de coordenadas para usar como destinos. |

**Ejemplo:**

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

**Ejemplo de Respuesta:**

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

Genera una matriz de distancia/duración y la convierte en un formato de grafo serializable (Nodos y Aristas) compatible con NetworkX y otras librerías de grafos.

**Cuerpo de la Solicitud (`MatrixRequest`):**
Igual que `POST /matrix`.

**Ejemplo de Respuesta:**

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

### 3. Emparejamiento de Mapas (Map Matching)

#### `POST /match`

Ajusta rastros de GPS con ruido a la red de carreteras. Maneja la división de rastros automáticamente si se pierde la señal.

**Ejemplo de Solicitud:**

```json
{
  "breadcrumbs": [
    {"longitude": -84.09, "latitude": 9.93, "timestamp": 1741185000},
    {"longitude": -84.091, "latitude": 9.931, "timestamp": 1741185010}
  ]
}
```

**Ejemplo de Respuesta:**

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

### 4. Optimización (TSP)

#### `POST /trip`

Resuelve el Problema del Viajante para encontrar la secuencia más eficiente para visitar múltiples coordenadas.

**Cuerpo de la Solicitud (`TripRequest`):**

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

**Ejemplo de Respuesta:**

Devuelve una geometría GeoJSON y la secuencia optimizada en `waypoints[].waypoint_index`.

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
    { "waypoint_index": 0, "location": [-84.09, 9.93], "name": "Inicio" },
    { "waypoint_index": 2, "location": [-84.05, 9.93], "name": "Parada 2" },
    { "waypoint_index": 1, "location": [-84.07, 9.91], "name": "Parada 1" }
  ]
}
```

---

### 5. Sistema

#### `GET /health`

Devuelve el estado del servicio y metadatos.

**Respuesta:**

```json
{
  "status": "healthy",
  "service": "osrm-microservice"
}
```

---

## Manejo de Errores

Todos los endpoints devuelven códigos de error HTTP estándar:

- **422 Unprocessable Entity**: Esquema de solicitud no válido (validación Pydantic).
- **500 Internal Server Error**: Fallo de la API OSRM descendente o error de lógica interna.
