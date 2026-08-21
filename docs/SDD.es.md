# Descripción de Diseño de Software

## Para OSRM API Gateway

**Versión 0.3.0**  
Preparado por Luis Valverde  
lvalverdeb  
2026-06-25

## Tabla de Contenidos

- [1. Introducción](#1-introducción)
  - [1.1 Propósito del Documento](#11-propósito-del-documento)
  - [1.2 Alcance del Proyecto](#12-alcance-del-proyecto)
  - [1.3 Definiciones, Acrónimos y Abreviaturas](#13-definiciones-acrónimos-y-abreviaturas)
  - [1.4 Referencias](#14-referencias)
  - [1.5 Resumen del Documento](#15-resumen-del-documento)
- [2. Visión General del Diseño](#2-visión-general-del-diseño)
  - [2.1 Intereses de los Interesados](#21-intereses-de-los-interesados)
  - [2.2 Puntos de Vista Seleccionados](#22-puntos-de-vista-seleccionados)
- [3. Vistas de Diseño](#3-vistas-de-diseño)
  - [3.1 Vista de Contexto](#31-vista-de-contexto)
  - [3.2 Vista de Composición](#32-vista-de-composición)
  - [3.3 Vista Lógica](#33-vista-lógica)
  - [3.4 Vista de Información](#34-vista-de-información)
  - [3.5 Vista de Interfaz](#35-vista-de-interfaz)
  - [3.6 Vista de Interacción](#36-vista-de-interacción)
  - [3.7 Vista de Algoritmo](#37-vista-de-algoritmo)
  - [3.8 Vista de Despliegue](#38-vista-de-despliegue)
  - [3.9 Vista de Concurrencia](#39-vista-de-concurrencia)
  - [3.10 Vista de Patrones](#310-vista-de-patrones)
- [4. Decisiones](#4-decisiones)
- [5. Apéndices](#5-apéndices)
  - [5.1 Formulación Matemática del VRP](#51-formulación-matemática-del-vrp)
  - [5.2 Configuración de Límites de Tasa](#52-configuración-de-límites-de-tasa)

---

## 1. Introducción

### 1.1 Propósito del Documento

Esta Descripción de Diseño de Software (SDD) define la arquitectura y el diseño del sistema OSRM API Gateway (v0.3.0). Sirve como referencia técnica principal para desarrolladores, mantenedores y operadores para comprender cómo está estructurado el sistema, cómo interactúan los componentes y cómo las decisiones de diseño se corresponden con los requisitos funcionales. El documento describe tanto las etapas de diseño preliminar (arquitectónico) como las de diseño detallado (a nivel de componente).

**Público objetivo:** Ingenieros de software, ingenieros DevOps, arquitectos técnicos, ingenieros de QA y futuros mantenedores del sistema.

### 1.2 Alcance del Proyecto

OSRM API Gateway es un microservicio asíncrono basado en FastAPI que envuelve el backend C++ de OSRM (Open Source Routing Machine), exponiendo capacidades especializadas de enrutamiento, coincidencia de mapas, optimización y resolución de Problemas de Ruteo de Vehículos (VRP) a través de una API RESTful JSON. Con enfoque geográfico en Costa Rica.

**Inclusiones:**
- API HTTP RESTful con 10 endpoints
- Proxy HTTP asíncrono al backend OSRM con pool de conexiones
- Procesamiento de trazas GPS con map matching
- Cálculo de matrices de distancia/duración y conversión a grafos
- Optimización del Problema del Viajante (TSP)
- Solucionador de VRP con clustering Location-Allocation
- Proxy de Mapbox Vector Tiles (MVT)
- Límites de tasa en todos los endpoints
- Tracing distribuido OpenTelemetry en todas las rutas de solicitud
- Caché distribuida respaldada por Redis para respuestas de ruta/matriz

**Exclusiones:**
- Internos del motor C++ de OSRM (procesamiento de datos, algoritmo de enrutamiento)
- Pipeline de procesamiento de datos OSM (manejado por deploy/docker/Dockerfile.builder)
- Visualización del lado del cliente (ejemplos provistos pero fuera del alcance)
- Autenticación/autorización

### 1.3 Definiciones, Acrónimos y Abreviaturas

| Término | Definición |
|---------|------------|
| API | Interfaz de Programación de Aplicaciones |
| MLD | Multi-Level Dijkstra - algoritmo de enrutamiento de OSRM |
| MVT | Mapbox Vector Tile - formato binario de teselas para datos cartográficos |
| OSRM | Open Source Routing Machine - motor de enrutamiento en C++ |
| SDD | Documento de Diseño de Software |
| TSP | Problema del Viajante - optimización de ruta para un vehículo |
| VRP | Problema de Ruteo de Vehículos - optimización de rutas para múltiples vehículos |
| Pydantic | Biblioteca de validación de datos en Python mediante anotaciones de tipo |
| FastAPI | Framework web asíncrono en Python para construir APIs |
| httpx | Cliente HTTP asíncrono para Python |
| NetworkX | Biblioteca de análisis de grafos en Python |
| Histéresis | Distancia de amortiguación que evita cambios de asignación cerca de los límites del depósito |
| Location-Allocation | Algoritmo de clustering que asigna paradas a depósitos óptimos |
| Euclidiana | Distancia en línea recta entre dos puntos |
| OTel | OpenTelemetry - framework de observabilidad para tracing distribuido |
| Redis | Servidor de diccionario remoto - almacén de estructuras de datos en memoria usado como caché |
| W3C TraceContext | Estándar para propagar contexto de trazado entre límites de servicio (header traceparent) |
| Cache-Aside | Patrón de caché de aplicación: leer de caché, en caso de fallo cargar fuente y poblar caché |

### 1.4 Referencias

| Referencia | Versión | Tipo |
|------------|---------|------|
| Documentación de la API OSRM v1 | v1.0 | Normativa |
| Documentación de FastAPI | 0.136.x | Normativa |
| Documentación de Pydantic v2 | 2.x | Normativa |
| IEEE Std 1016-2009 (SDD) | 2009 | Informativa |
| GitHub Spec Kit (Metodología SDD) | última | Informativa |
| GATEWAY_IMPLEMENTATION_PLAN.md | v0.2.2 | Normativa |
| API_REFERENCE.md | v0.2.2 | Normativa |
| docs/planning/vrp_proposal.md | v0.2.2 | Informativa |

### 1.5 Resumen del Documento

La Sección 2 presenta la visión general del diseño, los intereses de los interesados y los puntos de vista seleccionados. La Sección 3 contiene las vistas de diseño concretas (Contexto, Composición, Lógica, Información, Interfaz, Interacción, Algoritmo, Despliegue, Concurrencia, Patrones). La Sección 4 registra las decisiones arquitectónicas significativas. La Sección 5 contiene apéndices con material complementario.

---

## 2. Visión General del Diseño

### 2.1 Intereses de los Interesados

| Interesado | Intereses | Abordado Por |
|------------|-----------|--------------|
| Desarrolladores de Aplicación | Responsabilidades de componentes, APIs, modelos de datos | Vistas Lógica, Interfaz, Información |
| DevOps/SRE | Topología de despliegue, escalado, health checks | Vistas de Despliegue, Concurrencia |
| Ingenieros de QA | Testabilidad, manejo de errores, límites de tasa | Vistas de Interacción, Interfaz |
| Gerentes de Producto | Alcance de funcionalidades, capacidades VRP, cobertura de API | Vistas de Contexto, Composición |
| Futuros Mantenedores | Justificación del diseño, decisiones algorítmicas | Secciones de Algoritmo, Patrones, Decisiones |

### 2.2 Puntos de Vista Seleccionados

| Punto de Vista | Intereses Abordados |
|----------------|---------------------|
| Contexto | Límites del sistema, actores externos (Cliente, Backend OSRM) |
| Composición | Descomposición en módulos (servicios, modelos, capa de API) |
| Lógico | Jerarquía de clases, sistema de tipos, herencia de modelos Pydantic |
| Información | Esquemas de datos, modelos Pydantic, contratos request/response |
| Interfaz | Especificación de la API REST, contrato HTTP de OSRM |
| Interacción | Flujo de datos VRP, patrones de solicitud asíncrona, propagación de errores |
| Algoritmo | Location-Allocation VRP, división en fragmentos TSP, lógica de histéresis |
| Despliegue | Topología Docker Compose, builds multi-etapa, redes |
| Concurrencia | Modelo asíncrono E/S, pool de conexiones, límites de tasa |
| Patrones | Patrón Gateway, patrón Servicio, Inyección de Dependencias |

---

## 3. Vistas de Diseño

### 3.1 Vista de Contexto

**ID:** CTX-001  
**Título:** Contexto del Sistema  
**Punto de Vista:** Contexto  
**Representación:**

```
┌─────────────┐     HTTP/JSON      ┌───────────────────────────────────┐
│   Cliente   │ ──────────────────> │   OSRM API Gateway (Puerto 8000)  │
│ (App/Browser)│                    │  FastAPI + Uvicorn ASGI Server    │
│             │ <────────────────── │                                   │
└─────────────┘     HTTP/JSON      │  Endpoints: /route, /matrix,      │
                                     │  /match, /trip, /nearest, /tile,  │
                                     │  /vrp, /vrp/allocate, /health,    │
                                     │  /matrix-graph                    │
                                     └───────────┬───────────────────────┘
                                                 │ HTTP (httpx AsyncClient)
                                                 │ Puerto 5000
                                                 ▼
                                     ┌───────────────────────────────────┐
                                     │   Backend OSRM (Puerto 5000)      │
                                     │  Motor C++ (Algoritmo MLD)        │
                                     │  Perfiles: car, bicycle, foot     │
                                     │  Datos: costa-rica-latest.osrm    │
                                     └───────────────────────────────────┘
```

El sistema opera como una puerta de enlace entre los clientes y el backend C++ de OSRM. Los clientes interactúan exclusivamente con la puerta de enlace FastAPI en Python, que traduce solicitudes JSON de alto nivel en parámetros de consulta HTTP de OSRM y enriquece las respuestas con computación adicional (construcción de grafos, resolución VRP).

### 3.2 Vista de Composición

**ID:** CMP-001  
**Título:** Descomposición en Módulos  
**Punto de Vista:** Composición  
**Representación:**

```
┌───────────────────────────────────────────────────────────┐
│                  OSRM API Gateway                          │
├───────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Capa API (app/main.py)                              │  │
│  │  Aplicación FastAPI, 10 endpoints, manejo errores,   │  │
│  │  límites de tasa (slowapi), validación solicitudes   │  │
│  └──────────┬──────────────────────────────────────────┘  │
│             │                                             │
│    ┌────────┴─────────┬──────────────────┐               │
│    ▼                  ▼                  ▼                │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐│
│  │ OSRM     │  │ GraphBuilder │  │ VrpService            ││
│  │ Client   │  │ (graph_      │  │ (vrp_service.py)      ││
│  │ (osrm_   │  │  builder.py) │  │ Location-Allocation + ││
│  │ client   │  │ Grafo Di-    │  │ Optimización TSP      ││
│  │ .py)     │  │ recto Net-   │  │ Depende de:           ││
│  │ Proxy    │  │ workX desde  │  │ OSRMClient            ││
│  │ HTTP     │  │ matriz       │  │                       ││
│  │ Asíncrono│  │              │  │                       ││
│  └──────────┘  └──────────────┘  └──────────────────────┘│
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Capa de Modelos (app/models/schemas.py)             │  │
│  │  15 modelos Pydantic v2: Coordinate, Stop,          │  │
│  │  RouteReq, MatchReq, MatrixReq, TripReq,             │  │
│  │  NearestReq, VrpReq, VrpResponse, VehicleRoute, etc.│  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Capa de Configuración (app/config.py)               │  │
│  │  Pydantic Settings: OSRM_BASE_URL, límites tasa,    │  │
│  │  APP_NAME, cargados desde .env                       │  │
│  └─────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────┘
```

### 3.3 Vista Lógica

**ID:** LOG-001  
**Título:** Jerarquía de Tipos Central  
**Punto de Vista:** Lógico  
**Representación:**

```
┌─────────────────────────────┐
│  BaseModel (Pydantic)       │
└──────────┬──────────────────┘
           │
     ┌──────┴──────┐
     ▼             ▼
┌──────────┐ ┌─────────────┐
│Coordinate│ │CommonRouting│
│          │ │Options      │
│longitude │ │bearings     │
│latitude  │ │radiuses     │
└────┬─────┘ │hints        │
     │       │approaches   │
     ▼       │exclude      │
┌──────────┐ │snapping     │
│Stop      │ │skip_waypoints│
│id: str/int│└──────┬──────┘
└──────────┘        │
           ┌────────┼────────┬───────────┐
           ▼        ▼        ▼           ▼
     ┌────────┐┌────────┐┌────────┐ ┌─────────┐
     │RouteReq││MatchReq││Matrix  │ │TripReq  │
     │origin  ││bread-  ││Req     │ │coords   │
     │dest    ││crumbs  ││coords  │ │roundtrip│
     │waypoints││profile ││annot.  │ │source   │
     │steps   ││gaps    ││fallback│ │dest     │
     └────────┘│tidy    │└────────┘ └─────────┘
               └────────┘
                              ┌──────────────┐
                         ┌───>│VrpResponse   │
                         │    │code: str     │
                         │    │routes: List  │
                         │    │total_distance│
                         │    └──────────────┘
┌────────────┐            │
│VrpRequest  │────────────┤
│depots      │            │    ┌──────────────────┐
│stops       │────────────┼───>│VrpAllocationResp  │
│vehicle_ct  │            │    │code: str          │
│capacity    │            │    │allocations: Dict  │
│max_radius  │            │    │unreachable_stops  │
│clustering  │            │    └──────────────────┘
│hysteresis  │            │
│roundtrip   │            │    ┌──────────────┐
└────────────┘            └───>│VehicleRoute  │
                               │vehicle_id    │
                               │depot_index   │
                               │stops_indices │
                               │route_geometry│
                               │distance_meters│
                               │duration_secs │
                               └──────────────┘
```

**ID:** LOG-002  
**Título:** Clases de la Capa de Servicios  
**Punto de Vista:** Lógico  
**Representación:**

```
┌──────────────────────────┐
│ OSRMClient               │
├──────────────────────────┤
│ - base_url: str          │
│ - _client: AsyncClient   │
├──────────────────────────┤
│ + close()                │
│ + get_route(coords, req) │
│ + get_matrix(req)        │
│ + match_trace(req)       │
│ + get_trip(req)          │
│ + get_nearest(req)       │
│ + get_tile(profile,z,x,y)│
│ - _get(endpoint, params) │
│ + _serialize_common_opts │
└──────────────────────────┘

┌────────────────────────────────┐
│ GraphBuilder                   │
├────────────────────────────────┤
│ + build_from_matrix(data, req) │
│   → node_link_data (JSON)      │
└────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ VrpService                                       │
├──────────────────────────────────────────────────┤
│ - osrm_client: OSRMClient                        │
├──────────────────────────────────────────────────┤
│ + solve_vrp(req) → VrpResponse                   │
│ + allocate_products(req) → VrpAllocationResponse │
│ - _get_allocation_data(req)                      │
│ - _solve_tsp_chunk(...) → VehicleRoute           │
│ - _get_depot_to_stop_matrix(depots, stops)       │
│ - _allocate_stops(durations, distances, ...)     │
└──────────────────────────────────────────────────┘

┌────────────────────────┐
│ Settings               │
├────────────────────────┤
│ OSRM_BASE_URL: str     │
│ APP_NAME: str          │
│ DEBUG: bool            │
│ RATE_LIMIT_*: str      │
└────────────────────────┘
```

### 3.4 Vista de Información

**ID:** INF-001  
**Título:** Diccionario de Datos de Solicitud/Respuesta  
**Punto de Vista:** Información  
**Representación:**

| Modelo | Campo | Tipo | Restricciones | Descripción |
|--------|-------|------|---------------|-------------|
| Coordinate | longitude | float | [-180, 180] | Longitud WGS84 |
| Coordinate | latitude | float | [-90, 90] | Latitud WGS84 |
| Stop | id | str\|int\|null | opcional | Identificador único de parada |
| RouteRequest | origin | Coordinate | requerido | Punto de inicio |
| RouteRequest | destination | Coordinate | requerido | Punto de destino |
| RouteRequest | waypoints | List[Coord] | máx. 200 | Paradas intermedias |
| RouteRequest | profile | enum | driving/cycling/walking | Perfil de enrutamiento |
| RouteRequest | steps | bool | default true | Instrucciones paso a paso |
| RouteRequest | alternatives | bool\|int | default false | Rutas alternativas |
| MatchRequest | breadcrumbs | List[GPSBreadcrumb] | [2, 5000] | Puntos de traza GPS |
| MatrixRequest | coordinates | List[Coordinate] | [2, 5000], sources x destinations <= MATRIX_MAX_CELLS | Puntos para la matriz |
| MatrixRequest | annotations | enum | duration/distance/ambas | Métricas de costo |
| TripRequest | coordinates | List[Coordinate] | [2, 200] | Puntos a optimizar |
| VrpRequest | depots | List[Stop] | [1, 500] | Ubicaciones de almacenes |
| VrpRequest | stops | List[Stop] | [1, VRP_MAX_STOPS] | Puntos de entrega |
| VrpRequest | capacity | int | [1, 10000], default 35 | Capacidad por vehículo |
| VrpRequest | clustering_mode | enum | distance/travel_time/radial | Estrategia de asignación |
| VehicleRoute | route_geometry | Dict | GeoJSON | Ruta optimizada |
| VehicleRoute | distance_meters | float | >= 0 | Distancia total de la ruta |
| VehicleRoute | duration_seconds | float | >= 0 | Duración total de la ruta |

### 3.5 Vista de Interfaz

**ID:** INT-001  
**Título:** Superficie de la API REST  
**Punto de Vista:** Interfaz  
**Representación:**

| Método | Ruta | Cuerpo Solicitud | Respuesta | Límite de Tasa |
|--------|------|-----------------|-----------|----------------|
| GET | /health | — | `{status, service}` | — |
| POST | /route | RouteRequest | OSRM Route JSON | 600/min |
| POST | /matrix | MatrixRequest | OSRM Table JSON | 300/min |
| POST | /matrix-graph | MatrixRequest | `{nodes, edges}` | 300/min |
| POST | /match | MatchRequest | OSRM Match JSON | 600/min |
| POST | /trip | TripRequest | OSRM Trip JSON | 300/min |
| POST | /nearest | NearestRequest | OSRM Nearest JSON | 600/min |
| GET | /tile/{p}/{z}/{x}/{y}.mvt | — | application/x-protobuf | 600/min |
| POST | /vrp | VrpRequest | VrpResponse | 100/min |
| POST | /vrp/allocate | VrpRequest | VrpAllocationResponse | 100/min |

**Envoltorio de error común:**
```
HTTP 400/422/500
{"detail": {"code": "InvalidValue", "message": "..."}}
```

**Interfaz upstream OSRM** (interna, consumida por OSRMClient):
```
GET /{service}/v1/{profile}/{coordinates}?{params}
Services: route, table, match, trip, nearest, tile
Profiles: driving, cycling, walking
```

### 3.6 Vista de Interacción

**ID:** INT-ACT-001  
**Título:** Flujo de Resolución VRP  
**Punto de Vista:** Interacción  
**Representación:**

```
Cliente          API Gateway          OSRMClient          Backend OSRM
  │                  │                    │                   │
  │ POST /vrp        │                    │                   │
  │─────────────────>│                    │                   │
  │                  │                    │                   │
  │                  │ solve_vrp(req)     │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │                  │ _get_allocation    │                   │
  │                  │   _data(req)       │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │                  │ _get_depot_to_stop │                   │
  │                  │   _matrix(d,s)     │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /table/v1/... │
  │                  │                    │ [lotes de 500]   │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON (durations   │
  │                  │                    │  + distances)     │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return matrix      │                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │                  │ _allocate_stops(   │                   │
  │                  │   durations,       │                   │
  │                  │   distances, ...)  │                   │
  │                  │  ┌─────────────────┐                   │
  │                  │  │ Por cada parada: │                   │
  │                  │  │ 1. Encontrar     │                   │
  │                  │  │    mejor depósito│                   │
  │                  │  │    por métrica   │                   │
  │                  │  │ 2. Aplicar       │                   │
  │                  │  │    histéresis    │                   │
  │                  │  │ 3. Verificar     │                   │
  │                  │  │    cordura       │                   │
  │                  │  │ 4. Aplicar radio │                   │
  │                  │  │    máximo        │                   │
  │                  │  └─────────────────┘                   │
  │                  │                    │                   │
  │                  │ por cada cluster:  │                   │
  │                  │ _solve_tsp_chunk   │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /trip/v1/...  │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON (optimizado) │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return VehicleRoute│                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │                  │ VrpResponse        │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │ 200 JSON         │                    │                   │
  │<─────────────────│                    │                   │
```

**ID:** INT-ACT-002  
**Título:** Flujo Simple de Solicitud de Ruta  
**Punto de Vista:** Interacción  
**Representación:**

```
Cliente          API Gateway          OSRMClient          Backend OSRM
  │                  │                    │                   │
  │ POST /route      │                    │                   │
  │─────────────────>│                    │                   │
  │                  │ origin+dest+       │                   │
  │                  │ waypoints→coords   │                   │
  │                  │                    │                   │
  │                  │ get_route(coords,  │                   │
  │                  │   request)         │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /route/v1/... │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON response     │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return raw JSON    │                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │ 200 JSON         │                    │                   │
  │<─────────────────│                    │                   │
```

### 3.7 Vista de Algoritmo

**ID:** ALG-001  
**Título:** Location-Allocation VRP con Histéresis  
**Punto de Vista:** Algoritmo  
**Representación:**

**Propósito:** Asignar cada parada de entrega al depósito (almacén) óptimo considerando los costos de la red vial y la estabilidad.

**Entradas:**
- `durations`: matriz `[num_depots × num_stops]` de tiempos de viaje (segundos)
- `distances`: matriz `[num_depots × num_stops]` de distancias viales (metros)
- `depots`: lista de coordenadas de depósitos
- `stops`: lista de coordenadas de paradas
- `max_radius_m`: límite de distancia opcional
- `mode`: `"travel_time"` o `"distance"` o `"radial"`
- `hysteresis_m`: amortiguador que evita cambios (default 2000m)

**Pseudocódigo:**

```
1. SELECCIONAR target_matrix = durations si mode=="travel_time" sino distances
2. COMPUTAR distancias euclidianas desde cada parada a cada depósito
   (usando DEG_TO_M ≈ 110600m/grado, cos(lat) ≈ 0.98 para longitud)
3. POR cada parada s:
   a. anchor_depot = argmin(distancia euclidiana a s)
   b. SI mode == "radial":
        ASIGNAR s a anchor_depot; CONTINUAR
   c. best_depot = argmin(target_matrix[:, s])
   d. SI euclidiana(best_depot) - euclidiana(anchor) > 50km:
        USAR anchor_depot (anulación de cordura visual)
   e. SI best_val o anchor_val ≈ infinito:
        USAR el alcanzable
   f. SINO aplicar histéresis:
        SI target[best] < target[anchor] - hysteresis:
          ASIGNAR a best_depot
        SINO:
          ASIGNAR a anchor_depot
   g. SI max_radius y distance > max_radius:
        MARCAR como inalcanzable
      SINO:
        AGREGAR a asignación
4. RETORNAR {allocations, unreachable_stops}
```

**Conversión de histéresis (modo tiempo):** `effective_hysteresis = hysteresis_m / 11.1` segundos (≈ 2km a 40km/h).

**TSP por fragmentos (Fase 2):**

```
Por cada par (depósito, cluster):
  1. SUBDIVIDIR cluster en fragmentos de min(80, capacity)
  2. POR cada fragmento:
     a. CONSTRUIR TripRequest(depot + chunk, source="first",
                              destination="any", roundtrip=request.roundtrip)
     b. LLAMAR OSRM /trip/v1/{profile}/{coords}
     c. REORDENAR paradas según waypoint.trips_index y waypoint_index
     d. MAPEAR waypoints ordenados a índices originales de paradas
     e. RETORNAR VehicleRoute con geometry, distance, duration
  3. AGREGAR rutas en VrpResponse
```

### 3.8 Vista de Despliegue

**ID:** DEP-001  
**Título:** Topología Docker Compose  
**Punto de Vista:** Despliegue  
**Representación:**

```
┌─────────────────────────────────────────────────────────────┐
│                   Host Docker                                │
│                                                              │
│  ┌─ osrm-data-builder ─────────────────────────────────┐     │
│  │  Imagen: osrm-data-builder:latest                    │     │
│  │  Perfil: build (ejecución manual)                    │     │
│  │  CMD: osrm-extract → osrm-partition → osrm-customize │     │
│  │  Datos: costa-rica-latest.osm.pbf → archivos .osrm   │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ osrm ───────────────────────────────────────────────┐     │
│  │  Imagen: osrm-backend:latest (Dockerfile multi-etapa) │     │
│  │  Contenedor: osrm-backend                             │     │
│  │  Puerto: 5000 → 5000                                  │     │
│  │  CMD: osrm-routed --algorithm mld --max-trip-size 200 │     │
│  │  Perfiles: car (default), bicycle, foot               │     │
│  │  Volumen: /data/car/ ← archivos .osrm procesados     │     │
│  │  Plataforma: linux/amd64                              │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ api ────────────────────────────────────────────────┐     │
│  │  Imagen: osrm-api-gateway (Dockerfile)                │     │
│  │  Contenedor: osrm-api-gateway                         │     │
│  │  Puerto: 8080 → 8000 (FastAPI interno)                │     │
│  │  ENV: OSRM_BASE_URL=http://osrm-backend:5000          │     │
│  │  CMD: uvicorn app.main:app --host 0.0.0.0 --port 8000│     │
│  │  Depende de: osrm (health check)                      │     │
│  │  Reinicio: always                                     │     │
│  └──────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

**Flujo de datos en despliegue:** El contenedor `osrm-data-builder` se ejecuta una vez (perfil build) para procesar datos OSM PBF crudos en archivos de grafo de enrutamiento. El contenedor `osrm` carga estos archivos y sirve la API de enrutamiento. El contenedor `api` se conecta a `osrm` a través de la red interna de Docker.

### 3.9 Vista de Concurrencia

**ID:** CONC-001  
**Título:** E/S Asíncrona y Límites de Tasa  
**Punto de Vista:** Concurrencia  
**Representación:**

El sistema utiliza un modelo asíncrono de un solo hilo:

1. **Servidor ASGI:** Uvicorn ejecuta la aplicación FastAPI con workers asíncronos. Todos los manejadores de endpoints son `async def`.
2. **Pool de Clientes HTTP:** `OSRMClient` inicializa un único `httpx.AsyncClient` con pool de conexiones, reutilizado en todas las solicitudes. Timeout por defecto: 30s.
3. **Límites de Tasa:** Middleware `slowapi` con límites por endpoint aplicados mediante el decorador `@limiter.limit(...)`. Función clave: `get_remote_address` (el par inmediato; los despliegues pasan `--forwarded-allow-ips` para que el `X-Forwarded-For` de un proxy de confianza identifique al cliente real). Configuración:
   - `/route`: 600 req/min
   - `/matrix`, `/matrix-graph`: 300 req/min
   - `/match`: 600 req/min
   - `/trip`: 300 req/min
   - `/vrp`, `/vrp/allocate`: 100 req/min
   - `/nearest`, `/tile`: 600 req/min
4. **Sin hilos/paralelismo:** El servicio VRP procesa asignaciones y fragmentos TSP secuencialmente dentro de una sola solicitud. El batching de matrices (500 paradas/lote) usa llamadas `await` secuenciales.

### 3.10 Vista de Patrones

**ID:** PAT-001  
**Título:** Patrones Arquitectónicos Utilizados  
**Punto de Vista:** Patrones  
**Representación:**

| Patrón | Aplicación | Ubicación |
|--------|------------|-----------|
| **Gateway** | Punto único de entrada que traduce entre cliente y backend | `app/main.py` (todos los endpoints) |
| **Proxy** | Paso a través a OSRM con solicitud/respuesta enriquecida | Métodos de `OSRMClient` |
| **Capa de Servicio** | Lógica de negocio encapsulada en clases de servicio | `app/services/*` |
| **Inyección de Dependencias** | `VrpService` recibe `OSRMClient` mediante constructor | `VrpService.__init__(osrm_client)` |
| **Builder** | `GraphBuilder` construye un objeto complejo (grafo NetworkX) a partir de datos de matriz | `GraphBuilder.build_from_matrix()` |
| **Configuración** | Configuración basada en entorno mediante Pydantic BaseSettings | `app/config.py:Settings` |
| **Estrategia** | `clustering_mode` selecciona el algoritmo de asignación (distancia/tiempo/radial) | Parámetro mode de `_allocate_stops()` |
| **Procesamiento por Lotes** | Matrices grandes divididas en lotes de 500 paradas | `_get_depot_to_stop_matrix()` |
| **Cache-Aside** | La aplicación lee L1 (en memoria), en fallo lee L2 (Redis), en fallo consulta OSRM y popula ambas capas | `OSRMClient._get()` + `RedisCache` |
| **Tracing Distribuido OpenTelemetry** | Middleware de tracing que auto-instrumenta FastAPI y httpx, exportando spans vía OTLP | `app/tracing.py` |

---

## 4. Decisiones

**ID:** DEC-001  
**Título:** Arquitectura Gateway sobre Exposición Directa de OSRM  
**Contexto:** Los clientes podrían llamar a OSRM directamente, pero esto expondría parámetros de consulta HTTP sin procesar, carecería de límites de tasa y forzaría a cada cliente a implementar la codificación de parámetros delimitados por punto y coma de OSRM.  
**Opciones:** (a) Proxy directo OSRM, (b) Gateway completo con validación y enriquecimiento, (c) Capa GraphQL.  
**Resultado:** Opción elegida (b) — Gateway FastAPI completo con validación Pydantic, límites de tasa y cuerpos de solicitud/respuesta JSON enriquecidos. Proporciona una superficie de API estable y documentada.  
**Más Información:** El patrón Gateway simplifica la integración del cliente y centraliza las preocupaciones transversales.

**ID:** DEC-002  
**Título:** Asíncrono en Todo el Sistema  
**Contexto:** El sistema realiza muchas llamadas HTTP salientes que bloquean en E/S. Usar código síncrono limitaría el rendimiento bajo solicitudes concurrentes.  
**Opciones:** (a) Síncrono con pool de hilos, (b) Completamente asíncrono con FastAPI + httpx, (c) ASGI con endpoints síncronos.  
**Resultado:** Opción elegida (b) — Todos los endpoints y métodos de servicio son asíncronos, usando un único `httpx.AsyncClient` con pool de conexiones. Maximiza el rendimiento bajo carga concurrente.

**ID:** DEC-003  
**Título:** Arquitectura VRP: Asignación en Dos Fases + TSP  
**Contexto:** OSRM no soporta nativamente el enrutamiento multi-vehículo. Se necesitaba un solucionador VRP personalizado que delegara el enrutamiento al servicio `/trip` de OSRM.  
**Opciones:** (a) Llamadas OSRM trip puras por vehículo (sin asignación), (b) Location-Allocation + TSP, (c) Solucionador VRP externo (OR-Tools, jsprit).  
**Resultado:** Opción elegida (b) — Location-Allocation con clustering por histéresis, luego TSP por cluster vía OSRM `/trip`. Equilibra calidad algorítmica con reutilización de OSRM.  
**Más Información:** ver `docs/planning/vrp_proposal.md`.

**ID:** DEC-004  
**Título:** Estabilidad de Asignación Basada en Histéresis  
**Contexto:** Las paradas cerca de los límites de los depósitos podrían cambiar de asignación entre solicitudes debido a pequeñas variaciones de medición, causando rutas inconsistentes.  
**Opciones:** (a) Siempre elegir el depósito más cercano, (b) Amortiguador de histéresis, (c) Desempate aleatorio.  
**Resultado:** Opción elegida (b) — Un amortiguador de 2000m (configurable) evita cambios: una parada permanece con su depósito actual a menos que un depósito diferente sea `hysteresis_m` mejor. También incluye una verificación de cordura euclidiana de 50km.

**ID:** DEC-005  
**Título:** Modelos Pydantic v2 sobre Diccionarios Planos  
**Contexto:** Todas las solicitudes de API necesitan validación, serialización y documentación.  
**Opciones:** (a) Análisis de diccionarios planos, (b) Pydantic v1, (c) Pydantic v2, (d) Marshmallow.  
**Resultado:** Opción elegida (c) — Pydantic v2 con `BaseModel` y restricciones `Field` proporciona validación automática, generación de esquemas JSON para documentación OpenAPI y serialización rápida.

**ID:** DEC-006  
**Título:** Builds Docker Multi-Etapa para Procesamiento de Datos OSRM  
**Contexto:** La extracción de datos OSRM es lenta (~30min) y requiere herramientas diferentes al servidor de ejecución.  
**Opciones:** (a) Build de una sola etapa con todas las herramientas, (b) Multi-etapa con constructor y ejecución separados, (c) Volumen de datos pre-procesados.  
**Resultado:** Opción elegida (b) — Tres Dockerfiles en `deploy/docker/`: `Dockerfile.builder` (extraer/particionar/personalizar), `Dockerfile.osrm` (ejecución) y `Dockerfile` (gateway API). Solo las imágenes de ejecución se usan en producción.

**ID:** DEC-007  
**Título:** slowapi para Límites de Tasa  
**Contexto:** Los endpoints necesitan protección contra uso excesivo.  
**Opciones:** (a) Middleware personalizado, (b) slowapi, (c) Límites de tasa a nivel de Nginx.  
**Resultado:** Opción elegida (b) — `slowapi` con seguimiento de tasa en memoria. Configuración simple por endpoint mediante decoradores. Nginx podría añadirse aguas arriba para despliegues distribuidos.

**ID:** DEC-008  
**Título:** Reintentos con Backoff Exponencial para Fallos Transitorios de OSRM  
**Contexto:** OSRM puede devolver 5xx esporádicos o timeouts bajo carga. Un solo fallo propagaría un error 500 al cliente.  
**Opciones:** (a) Dejar propagar fallos, (b) Reintentos con retardo fijo, (c) Backoff exponencial con jitter.  
**Resultado:** Opción elegida (c) — `tenacity` con backoff exponencial (1s → 10s máximo, 3 intentos). Solo reintenta en 5xx, timeouts y errores de transporte. Los errores 4xx nunca se reintentan.

**ID:** DEC-009  
**Título:** Caché de Respuestas para Consultas OSRM Repetidas  
**Contexto:** La misma solicitud de ruta o matriz puede enviarse múltiples veces en minutos. Llamadas innecesarias a OSRM desperdician recursos.  
**Opciones:** (a) Sin caché, (b) Caché en memoria con TTL, (c) Caché Redis.  
**Resultado:** Opción elegida (b) — `cachetools.TTLCache` con TTL de 15 minutos y límite de 1024 entradas. Estrategia cache-first: `_get` retorna datos cacheados inmediatamente, pasando a OSRM en caso de fallo.

**ID:** DEC-010  
**Título:** Métricas Prometheus para Observabilidad  
**Contexto:** El sistema no tenía visibilidad de latencia, tasa de error ni throughput.  
**Opciones:** (a) Sin métricas, (b) Prometheus client con instrumentación personalizada, (c) Exportador OpenTelemetry.  
**Resultado:** Opción elegida (b) — `prometheus-fastapi-instrumentator` auto-instrumenta todos los endpoints. Expone `/metrics` en formato Prometheus.

**ID:** DEC-011  
**Título:** FastAPI Lifespan para Cierre Gradual del Pool de Conexiones  
**Contexto:** El pool de `httpx.AsyncClient` nunca se cerraba explícitamente, causando warnings de transportes no cerrados.  
**Resultado:** `OSRMClient.close()` conectado al context manager `lifespan` de FastAPI. El pool se desarma graciosamente al detener el servidor ASGI.

**ID:** DEC-012  
**Título:** Tracing Distribuido OpenTelemetry  
**Contexto:** El sistema abarca dos servicios y realiza múltiples llamadas HTTP salientes por solicitud. Se necesita visibilidad de desglose de latencia y correlación extremo a extremo.  
**Opciones:** (a) Sin tracing, (b) IDs de correlación personalizados, (c) OpenTelemetry con W3C TraceContext.  
**Resultado:** Opción elegida (c) — SDK OpenTelemetry con `opentelemetry-instrumentation-fastapi` y `opentelemetry-instrumentation-httpx`. Spans exportados via OTLP. Headers W3C `traceparent` propagados al backend OSRM.

**ID:** DEC-013  
**Título:** Caché Distribuida Respaldada por Redis  
**Contexto:** La caché TTLCache en memoria (DEC-009) se pierde al reiniciar, no se comparte entre réplicas, y tiene límite de 1024 entradas.  
**Opciones:** (a) Solo en memoria, (b) Redis como L2 detrás de L1 en memoria, (c) Solo Redis.  
**Resultado:** Opción elegida (b) — Dos niveles: L1 es `cachetools.TTLCache` (lecturas locales sub-milisegundo), L2 es Redis (compartido entre instancias, sobrevive reinicios). Patrón Cache-Aside: `_get()` consulta L1 → L2 → OSRM.

---

## 5. Apéndices

### 5.1 Formulación Matemática del VRP

**Fase de Location-Allocation:**

Dados depósitos `D = {d₁, ..., dₘ}` y paradas `S = {s₁, ..., sₙ}`, con matriz de costos `C ∈ ℝ^{m×n}` donde `C[i][j]` es el tiempo de viaje (o distancia) desde el depósito `dᵢ` a la parada `sⱼ`:

Asignar cada parada `sⱼ` exactamente a un depósito `dᵢ` tal que:

```
asignación(sⱼ) = dᵢ  donde  i = argmin_k C[k][j]
```

Sujeto a:
- **Histéresis:** `C[best][j] < C[anchor][j] - h` para reasignación
- **Cordura:** `euclidiana(best) - euclidiana(anchor) < 50km`
- **Radio máximo:** `distancia_vial(i, j) ≤ max_radius_km`
- **Modo radial:** Usa distancia euclidiana en lugar de costo vial

**Fase TSP (por cluster):**

Para cada depósito `d` con paradas asignadas `S' = {s'₁, ..., s'ₖ}`:

Encontrar permutación `π` que minimice el costo total de ida y vuelta:

```
minimizar     distance(d, s'_{π₁}) + Σ_{t=1}^{k-1} distance(s'_{πₜ}, s'_{π_{t+1}}) + distance(s'_{πₖ}, d)
sujeto a     1 ≤ πₜ ≤ k,  ∀t
             πₜ ≠ πₛ  para t ≠ s
```

Delegado al servicio OSRM `/trip` que utiliza heurísticas especializadas sobre la jerarquía de contracción.

### 5.2 Configuración de Límites de Tasa

| Endpoint | Límite | Ventana |
|----------|--------|---------|
| /route | 600 | 1 minuto |
| /matrix | 300 | 1 minuto |
| /matrix-graph | 300 | 1 minuto |
| /match | 600 | 1 minuto |
| /trip | 300 | 1 minuto |
| /nearest | 600 | 1 minuto |
| /tile | 600 | 1 minuto |
| /vrp | 100 | 1 minuto |
| /vrp/allocate | 100 | 1 minuto |

Configurado mediante `app/config.py:Settings` con anulación por variables de entorno.
