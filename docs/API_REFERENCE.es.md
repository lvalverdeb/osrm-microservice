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

### `CommonRoutingOptions`

Opciones generales opcionales de OSRM aplicables a los servicios Route, Table, Match y Trip.

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `bearings` | `List[str]` | Restricciones de orientación por coordenada como cadenas 'ángulo,desviación'. |
| `radiuses` | `List[float]` | Radio de ajuste por coordenada en metros. Use `null` para ilimitado. |
| `hints` | `List[str]` | Cadenas de sugerencia por coordenada de una respuesta OSRM anterior. |
| `approaches` | `List[str]` | Lado de aproximación por coordenada: `unrestricted` o `curb`. |
| `exclude` | `List[str]` | Clases de carreteras a excluir globalmente (ej. `['motorway', 'toll']`). |
| `snapping` | `str` | Selección de bordes: `default` o `any`. |
| `skip_waypoints` | `bool` | Suprimir el array de waypoints en la respuesta. |

### `RouteRequest` (Hereda de `CommonRoutingOptions`)

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `origin` | `Coordinate` | Punto de inicio de la ruta. |
| `destination` | `Coordinate` | Punto de destino final. |
| `waypoints` | `List[Coordinate]` | Puntos intermedios opcionales para pasar. |
| `profile` | `str` | Perfil de enrutamiento: `driving` (predeterminado), `cycling`, `walking`. |
| `alternatives` | `bool o int` | Devolver alternativas (booleano) o un número específico (entero). |
| `overview` | `str` | Resolución de la geometría: `simplified` (predeterminado), `full`, `false`. |
| `geometries` | `str` | Formato de geometría: `polyline` (predeterminado), `polyline6`, `geojson`. |
| `steps` | `bool` | Devolver instrucciones de giro paso a paso (Predeterminado: `true`). |
| `annotations` | `str` | Metadatos separados por comas por segmento (ej. `distance,duration`). |

### `MatrixRequest` (Hereda de `CommonRoutingOptions`)

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Lista de puntos a incluir en el cálculo. |
| `profile` | `str` | Perfil de enrutamiento: `driving`, `cycling`, `walking`. |
| `sources` | `List[int]` | Índices de puntos para usar como orígenes. |
| `destinations` | `List[int]` | Índices de puntos para usar como destinos. |
| `annotations` | `str` | `duration` (predeterminado), `distance`, o `duration,distance`. |

### `MatchRequest` (Hereda de `CommonRoutingOptions`)

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Secuencia de puntos para ajustar a la red de carreteras. |
| `profile` | `str` | Perfil de enrutamiento: `driving`, `cycling`, `walking`. |
| `overview` | `str` | Resolución de la geometría: `simplified`, `full`, `false`. |
| `geometries` | `str` | Formato de geometría: `polyline`, `polyline6`, `geojson`. |
| `steps` | `bool` | Devolver pasos para la ruta ajustada. |
| `annotations` | `str` | Metadatos separados por comas por segmento. |
| `gaps` | `str` | Dividir ruta en brechas grandes: `split` o `ignore`. |
| `tidy` | `bool` | Eliminar coordenadas repetidas o fuera de orden antes de ajustar. |
| `match_waypoints` | `List[int]` | Índices de breadcrumbs para tratar como waypoints explícitos. |

### `GPSBreadcrumb`

Un punto de trazado GPS individual.

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitud del punto. |
| `latitude` | `float` | Latitud del punto. |
| `timestamp` | `int` | Marca de tiempo Unix. |
| `accuracy_meters` | `float` | Radio de ajuste / precisión en metros (Predeterminado: `5.0`). |

### `Stop` (Hereda de `Coordinate`)

Una parada de entrega geográfica o ubicación de depósito con identificación.

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitud del punto. |
| `latitude` | `float` | Latitud del punto. |
| `id` | `str o int` | Identificador único opcional para seguimiento. |

### `TripRequest` (Hereda de `CommonRoutingOptions`)

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Coordenadas a optimizar. |
| `roundtrip` | `bool` | Volver al primer punto al final (Predeterminado: `true`). |
| `source` | `str` | Restricción de inicio: `first` o `any`. |
| `destination` | `str` | Restricción de fin: `last` o `any`. |
| `profile` | `str` | Perfil de enrutamiento: `driving`, `cycling`, `walking`. |
| `overview` | `str` | Resolución de la geometría: `simplified`, `full`, `false`. |
| `geometries` | `str` | Formato de geometría: `polyline`, `polyline6`, `geojson`. |
| `steps` | `bool` | Devolver instrucciones paso a paso. |
| `annotations` | `str` | Metadatos separados por comas por segmento. |

### `NearestRequest` (Hereda de `CommonRoutingOptions`)

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinate` | `Coordinate` | Punto a ajustar a la red. |
| `number` | `int` | Número de segmentos de carretera más cercanos a devolver (Predeterminado: 1). |
| `profile` | `str` | Perfil de enrutamiento: `driving`, `cycling`, `walking`. |

### `NearestResponse`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `code` | `str` | Código de estado de la operación (ej. `Ok`). |
| `waypoints` | `List[Dict]` | Metadatos de los segmentos de carretera ajustados. |

### `VrpRequest`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `depots` | `List[Stop]` | Lista de almacenes / depósitos. |
| `stops` | `List[Stop]` | Lista de paradas de entrega. |
| `vehicle_count` | `int` | Número de vehículos disponibles. Predeterminado a uno por depósito. |
| `capacity` | `int` | Máximo de paradas/paquetes por vehículo (Predeterminado: 35). |
| `max_radius_km` | `float` | Distancia máxima por carretera desde el depósito (km) opcional. |
| `clustering_mode` | `str` | Tipo de agrupación: `travel_time` (predeterminado), `distance` o `radial`. |
| `hysteresis_m` | `float` | Tolerancia del límite de depósito en metros (Predeterminado: `2000.0`). |
| `roundtrip` | `bool` | Regresar al depósito al finalizar la ruta (Predeterminado: `true`). |

### `VehicleRoute`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `vehicle_id` | `str o int` | Identificador del vehículo (con sufijo). |
| `depot_index` | `int` | Índice del almacén asignado. |
| `stops_indices` | `List[int]` | Secuencia optimizada de índices de paradas. |
| `stop_ids` | `List[str o int]` | Lista opcional de IDs de paradas en orden optimizado. |
| `stop_coordinates` | `List[Coordinate]` | Coordenadas en orden optimizado. |
| `route_geometry` | `Dict` | Geometría GeoJSON LineString de la ruta. |
| `distance_meters` | `float` | Distancia total en metros. |
| `duration_seconds` | `float` | Duración total en segundos. |

### `VrpResponse`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `code` | `str` | Código de estado de la respuesta. |
| `routes` | `List[VehicleRoute]` | Rutas optimizadas por vehículo. |
| `total_distance` | `float` | Distancia total de toda la flota. |
| `total_duration` | `float` | Tiempo total de viaje de toda la flota. |

### `VrpAllocationResponse`

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `code` | `str` | Código de estado de la respuesta. |
| `allocations` | `Dict[str/int, List]` | Identificadores de depósito mapeados a arrays de paradas asignadas. |
| `unreachable_stops` | `List` | Lista de IDs/índices de paradas que exceden los límites. |

---

## Endpoints

### Endpoints del Sistema

#### `GET /health`

Verifica si la pasarela (gateway) está activa.

**Cuerpo de la Respuesta:**
```json
{
  "status": "healthy",
  "service": "osrm-api-gateway"
}
```

---

### Endpoints de Enrutamiento (Routing)

#### `POST /route`

Calcula la ruta más rápida entre coordenadas.

**Cuerpo de la Solicitud (`RouteRequest`):**
```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "profile": "walking",
  "steps": true
}
```

**Cuerpo de la Respuesta (JSON):** Pasa directamente el resultado del servicio `/route` de OSRM que contiene `code`, `routes` y `waypoints`.

---

#### `POST /nearest`

Ajusta una coordenada a los segmentos de carretera más cercanos.

**Cuerpo de la Solicitud (`NearestRequest`):**
```json
{
  "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
  "number": 1,
  "profile": "driving"
}
```

**Cuerpo de la Respuesta (`NearestResponse`):**
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

### Endpoints de Matriz

#### `POST /matrix`

Calcula los tiempos y distancias de viaje entre todas las ubicaciones suministradas.

**Cuerpo de la Solicitud (`MatrixRequest`):**
```json
{
  "coordinates": [
    {"longitude": -84.0907, "latitude": 9.9281},
    {"longitude": -84.0833, "latitude": 9.9333}
  ],
  "profile": "driving"
}
```

**Cuerpo de la Respuesta:** Pasa directamente el resultado del servicio `/table` de OSRM que contiene `code`, `durations`, `distances`, `sources` y `destinations`.

---

#### `POST /matrix-graph`

Construye una representación serializable en grafo dirigido de la matriz.

**Cuerpo de la Solicitud (`MatrixRequest`):** Igual que `POST /matrix`.

**Cuerpo de la Respuesta (`MatrixGraphResponse`):**
```json
{
  "nodes": [{"id": 0, "lon": -84.0907, "lat": 9.9281}],
  "edges": [{"source": 0, "target": 1, "duration": 180.0, "distance": 1200.0}]
}
```

---

### Endpoints de Ajuste de Mapa (Map Matching)

#### `POST /match`

Ajusta puntos GPS con ruido a la red de carreteras.

**Cuerpo de la Solicitud (`MatchRequest`):**
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

**Cuerpo de la Respuesta:** Pasa directamente el resultado del servicio `/match` de OSRM que contiene `code`, `matchings` y `tracepoints`.

---

### Endpoints de Optimización

#### `POST /trip`

Optimiza una secuencia de paradas (Problema del Agente Viajero - TSP).

**Cuerpo de la Solicitud (`TripRequest`):**
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

**Cuerpo de la Respuesta:** Pasa directamente el resultado del servicio `/trip` de OSRM que contiene `code`, `trips` y `waypoints`.

---

#### `POST /vrp`

Resuelve Problemas de Enrutamiento de Vehículos (VRP) multivehículo utilizando la agrupación de Localización-Asignación.

**Cuerpo de la Solicitud (`VrpRequest`):**
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

**Cuerpo de la Respuesta (`VrpResponse`):**
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

Pre-agrupa paradas en depósitos antes de enrutar (ideal para verificar asignaciones).

**Cuerpo de la Solicitud (`VrpRequest`):** Mismo que `POST /vrp`.

**Cuerpo de la Respuesta (`VrpAllocationResponse`):**
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

### Endpoints de Teselas (Tiles)

#### `GET /tile/{profile}/{z}/{x}/{y}.mvt`

Proxy de teselas vectoriales Mapbox del backend de OSRM. Nivel de zoom mínimo: 12.

---

## Manejo de Errores

El servicio devuelve cuerpos de error estructurados de OSRM cuando están disponibles:

```json
{
  "detail": {
    "code": "NoRoute",
    "message": "Could not find a route between coordinates"
  }
}
```
