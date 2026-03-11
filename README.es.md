# Microservicio Backend OSRM

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

```bash
# Apuntar al host remoto
export DOCKER_HOST=tcp://10.211.55.28:2375

# Construir e iniciar los servicios
docker compose up -d --build
```

## Herramientas de Visualización

El proyecto incluye herramientas de Python para visualizar y comparar rutas:

- **`examples/routing/visualize_routes.py`**: Obtiene y traza rutas principales y alternativas para un viaje.
- **`examples/benchmarking/compare_tsp.py`**: Compara una secuencia de paradas proporcionada (Real) con un viaje de ida y vuelta optimizado por TSP (Optimizado).

**Uso**:

```bash
# Generar un mapa de comparación para un viaje de ida y vuelta
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
- **FastAPI Gateway**: API de Python asíncrona que proporciona endpoints especializados para el emparejamiento de mapas y la generación de grafos.
- **Integración con NetworkX**: Convierte de forma transparente las salidas de la matriz en grafos serializables.
