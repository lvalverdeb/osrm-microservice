# Microservicio Backend OSRM

[English](https://github.com/lvalverde/osrm-microservice/blob/main/README.md) | [Español](https://github.com/lvalverde/osrm-microservice/blob/main/README.es.md) | [Français](https://github.com/lvalverde/osrm-microservice/blob/main/README.fr.md)

Enrutamiento de alto rendimiento y emparejamiento de mapas (map-matching) para Costa Rica.

## Instrucciones de Configuración

Este proyecto utiliza un flujo de trabajo de **Construcción Local y Transferencia Agrupada** para admitir el despliegue en hosts Docker remotos mientras se procesan los datos localmente en macOS.

### 1. Requisitos Previos

- Docker Desktop (macOS)
- Host Docker Remoto (ej. VM Linux en `10.211.55.28`)
- `make`

### 2. Adquisición de Datos y Procesamiento Local

Extraiga y procese los datos OSM de Costa Rica localmente. Este proceso agrupa los datos en su carpeta local `./data` utilizando un constructor basado en Docker "Sin Montaje".

```bash
# Descargar los últimos datos del mapa de Costa Rica
make download-data

# Procesar los datos localmente para un perfil específico (car, bicycle, foot)
# Por defecto es car si se omite PROFILE
make process-osrm PROFILE=car
```

### 3. Despliegue Remoto

Despliegue la API y el motor OSRM en el host remoto. Los datos procesados se agrupan directamente desde la imagen del constructor a la imagen de tiempo de ejecución OSRM a través de un `Dockerfile.osrm` de múltiples etapas.

`ghcr.io/project-osrm/osrm-backend` es actualmente solo `amd64`. Confirme la arquitectura del daemon Docker activo antes de iniciar los servicios.

```bash
# Apuntar al host remoto
export DOCKER_HOST=tcp://10.211.55.28:2375

# Verificar host objetivo y arquitectura
make compose-doctor

# Construir e iniciar servicios con secuencia segura + chequeos de salud
make compose-up

# Ver logs de servicios
make compose-logs

# Detener servicios
make compose-down
```

Evite ejecutar `docker compose down & docker compose up --build`; `&` manda el primer comando al fondo y puede causar condiciones de carrera.

## Servicios Principales

La aplicación encapsula la lógica de enrutamiento compleja en varios servicios clave ubicados en `app/services/`:

### 1. Cliente OSRM (`osrm_client.py`)
Un cliente HTTP asíncrono que interactúa directamente con el backend OSRM en C++. Formatea las consultas y estandariza las respuestas.
**Ejemplo de Caso de Uso**: Obtener la geometría exacta y las instrucciones de conducción para un viaje entre un almacén y múltiples puntos de entrega.

### 2. Constructor de Grafos (`graph_builder.py`)
Transforma las matrices de distancia y duración en bruto de OSRM en grafos dirigidos de `NetworkX`.
**Ejemplo de Caso de Uso**: Generar una representación matemática de la red de carreteras para alimentar algoritmos de optimización avanzados (como resolutores TSP personalizados) o para identificar nodos aislados en la red de entrega.

### 3. Servicio VRP (`vrp_service.py`)
Un solucionador integral de Problemas de Enrutamiento de Vehículos (VRP). Implementa una estrategia de Localización-Asignación, asignando paradas de entrega al almacén (depósito) disponible más cercano y generando secuencias de entrega optimizadas.
**Ejemplo de Caso de Uso**: Una empresa de logística quiere distribuir 500 paquetes diarios entre 5 conductores que parten de 2 almacenes diferentes, asegurando que cada conductor tome el grupo de paradas más óptimo.

## Ejemplos de Uso para Aplicaciones Cliente

A continuación se muestran ejemplos de cómo una aplicación cliente puede interactuar con el microservicio FastAPI utilizando la biblioteca `requests` de Python:

```python
import requests

BASE_URL = "http://localhost:8000"

# 1. Trazado de Rutas (Route Plotting)
route_payload = {
    "origin": {"lat": 9.9281, "lon": -84.0907},
    "destination": {"lat": 9.9333, "lon": -84.0833},
    "alternatives": True
}
route_res = requests.post(f"{BASE_URL}/route", json=route_payload)

# 2. Problema del Agente Viajero (TSP)
tsp_payload = {
    "locations": [
        {"lat": 9.9281, "lon": -84.0907},
        {"lat": 9.9333, "lon": -84.0833},
        {"lat": 9.9981, "lon": -84.1107}
    ]
}
tsp_res = requests.post(f"{BASE_URL}/trip", json=tsp_payload)

# 3. Agrupamiento (Clustering / Asignación)
cluster_payload = {
    "depots": [{"id": "D1", "lat": 9.9281, "lon": -84.0907}],
    "locations": [
        {"id": "L1", "lat": 9.9333, "lon": -84.0833},
        {"id": "L2", "lat": 9.9981, "lon": -84.1107}
    ],
    "num_vehicles": 2
}
cluster_res = requests.post(f"{BASE_URL}/vrp/allocate", json=cluster_payload)

# 4. Problema de Enrutamiento de Vehículos (VRP)
vrp_payload = {
    "depots": [{"id": "D1", "lat": 9.9281, "lon": -84.0907}],
    "locations": [
        {"id": "L1", "lat": 9.9333, "lon": -84.0833},
        {"id": "L2", "lat": 9.9981, "lon": -84.1107}
    ],
    "num_vehicles": 2
}
vrp_res = requests.post(f"{BASE_URL}/vrp", json=vrp_payload)
```

## Herramientas de Visualización

El proyecto incluye herramientas de Python para visualizar y comparar rutas:

- **`examples/routing/visualize_routes.py`**: Obtiene y traza rutas principales y alternativas para un viaje.
- **`examples/benchmarking/compare_tsp.py`**: Compara una secuencia de paradas proporcionada (Real) con un viaje de ida y vuelta optimizado por TSP (Optimizado).
- **`examples/clustering/simple_id_example.py`**: Un demostrador de VRP completo que simula 10 vehículos en múltiples depósitos y genera un mapa interactivo de Folium.

**Uso**:

```bash
# Ejecutar la simulación VRP de 10 vehículos
uv run examples/clustering/simple_id_example.py

# Comparar secuencias reales vs optimizadas
uv run examples/benchmarking/compare_tsp.py
```

Los mapas se guardan como archivos HTML interactivos (`map.html`, `comparison_map.html`).

## Documentación de la API

La documentación interactiva está disponible una vez que el servicio se está ejecutando:

- Swagger UI: `http://localhost:8000/docs`
- Redoc: `http://localhost:8000/redoc`

Para una guía detallada para desarrolladores, consulte:

- [Referencia de la API (Inglés)](docs/API_REFERENCE.md)
- [Referencia de la API (Español)](docs/API_REFERENCE.es.md)
- [Référence API (Francés)](docs/API_REFERENCE.fr.md)

## Componentes

- **Motor OSRM**: Potencia de enrutamiento en C++ que ejecuta el algoritmo MLD.
- **FastAPI Gateway**: API de Python asíncrona que proporciona endpoints especializados para el emparejamiento de mapas, la generación de grafos y Problemas de Enrutamiento de Vehículos (VRP).
- **Resoledor VRP**: Motor de Localización-Asignación para la agrupación multivehículo con soporte para IDs personalizados y división de rutas basada en capacidad.
- **Integración con NetworkX**: Convierte de forma transparente las salidas de la matriz en grafos serializables.
