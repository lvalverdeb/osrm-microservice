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

### `NearestRequest` (Hereda de `CommonRoutingOptions`)

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `coordinate` | `Coordinate` | Punto a ajustar a la red. |
| `number` | `int` | Número de segmentos de carretera más cercanos a devolver (Predeterminado: 1). |
| `profile` | `str` | Perfil de enrutamiento: `driving`, `cycling`, `walking`. |

---

## Endpoints

### 1. Enrutamiento (Routing)

#### `POST /route`

Calcula la ruta más rápida entre puntos.

**Ejemplo de Solicitud (`RouteRequest`):**

```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "profile": "walking",
  "steps": true
}
```

---

### 5. Nearest (Ajuste a Carretera)

#### `POST /nearest`

Encuentra el punto o puntos de la red de carreteras más cercanos a una coordenada dada.

**Ejemplo de Solicitud (`NearestRequest`):**

```json
{
  "coordinate": {"longitude": -84.09, "latitude": 9.93},
  "number": 3,
  "profile": "cycling"
}
```

---

### 6. Teselas (Mapbox Vector Tiles)

#### `GET /tile/{profile}/{z}/{x}/{y}.mvt`

Proxy de una tesela vectorial Mapbox desde OSRM. Nivel de zoom mínimo: 12.

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
