# Qué hace realmente el VRP

Documento de referencia para quien deba explicar, integrar, vender, operar o
extender la capacidad de ruteo de vehículos de este repositorio.

> **English version:** [tier2/40-what-the-vrp-does.md](../tier2/40-what-the-vrp-does.md). Ambos
> documentos se mantienen en paralelo; si divergen, el inglés es el que se
> escribió contra el código.

**Si no sabe qué es un problema de ruteo de vehículos, empiece por la Parte 0.**
Explica el problema en lenguaje llano, con operaciones reales como ejemplo, y no
da nada por sabido. El resto del documento sí lo da.

**Después lea esto: dos cosas distintas dentro de este repositorio se llaman "el
VRP", y no son versiones la una de la otra.** Casi toda la confusión sobre lo
que hace este sistema nace de mezclarlas.

| | `POST /vrp` en el gateway | La plataforma `vrp/` en Python |
|---|---|---|
| Qué es | Dos endpoints HTTP sobre el gateway en Rust | Un modelo de dominio, un evaluador, un verificador y un conjunto de solvers |
| Dónde | `gateway/src/vrp/` -- 1,034 líneas de Rust | [`vrp/`](../tier1/08-module-map.md) -- una biblioteca en Python |
| ¿Desplegado? | **Sí.** Hoy está atendiendo tráfico | **No.** Es una biblioteca; ningún proceso la expone |
| Qué optimiza | Cuál depósito atiende cada parada, y en qué orden las recorre un vehículo | Todo lo de abajo: ventanas, jornadas, competencias, horas de conducción, carga, premios, batería |
| ¿Entiende el tiempo? | No. Sin ventanas, sin duración de servicio, sin reloj | Sí. Ventanas, tiempo de servicio, jornadas, horas de conducción, viaje dependiente de la hora |
| ¿Entiende la carga? | No. `capacity` significa *paradas por vehículo* | Sí. Cantidades multidimensionales contra capacidades por vehículo |
| Solver | El `/trip` de OSRM (una heurística de TSP) | Adaptadores a PyVRP y OR-Tools, un LNS propio, pulido por partición de conjuntos, descomposición |
| Calidad de la respuesta | Entre 8.2% y 14.8% peor que un solver real, medido | La referencia contra la que se midió el gateway |
| Estado | Producción | 84 de 86 tareas del backlog hechas; 2 bloqueadas, ninguna por esfuerzo |

Si alguien pregunta "¿su VRP maneja ventanas de entrega?", la respuesta es **el
endpoint desplegado no, y la biblioteca sí.** Todo lo demás en este documento
desarrolla esa frase.

---

# Parte 0 -- Qué es un problema de ruteo de vehículos

*Sáltese esta parte si ya trabaja con software de ruteo. Está aquí para que un
despachador, un gerente financiero o alguien recién incorporado pueda leer el
resto.*

## 0.1 El problema, en un párrafo

Usted opera una bodega. Esta mañana tiene 600 paquetes que deben llegar hoy a
600 direcciones distintas, y cuenta con seis camiones y seis conductores.
**¿Cuáles paquetes van en cuál camión, y en qué orden los recorre cada
conductor?**

Eso es un problema de ruteo de vehículos. Todo lo demás -- capacidades, ventanas
de entrega, jornadas laborales, competencias del personal -- son detalles que se
montan encima de esas dos preguntas. Un software de ruteo es una máquina para
responderlas.

El nombre es literal y tiene casi la misma edad que la computación comercial:
Dantzig y Ramser lo plantearon en 1959 como "el problema del despacho de
camiones". Las preguntas no han cambiado. Las flotas se hicieron más grandes.

## 0.2 No es la misma pregunta que responde una app de mapas

Este es el malentendido más común, y vale la pena ser preciso.

| | Una app de mapas | Un problema de ruteo de vehículos |
|---|---|---|
| Pregunta | ¿Cómo llego de **A a B**? | Dados **600 B**, seis camiones y una bodega: ¿quién lleva cuál y en qué orden? |
| Respuesta | Una ruta | Una asignación *y* un orden para cada vehículo |
| Qué hay que decidir | Cuáles calles | Cuál vehículo, en qué posición de su día, por cuáles calles |
| Uso típico | El conductor, en el momento de manejar | El planificador, la noche anterior |

Una app de mapas es un componente *dentro* de un sistema de ruteo: es lo que le
dice que el trayecto de la parada 12 a la 13 toma nueve minutos. No decide que
la parada 13 deba ir después de la 12, ni que pertenezcan al mismo conductor.

En este repositorio esa división es literal: **OSRM es el motor de mapas** y
responde "qué tan lejos, cuánto tarda, por cuáles calles". **El VRP es la capa
que decide quién va a dónde.**

## 0.3 Por qué es difícil: los números se disparan rapidísimo

Déle diez paradas a un camión y hay 362,880 órdenes posibles de recorrerlas.
Con veinte paradas son 121,645,100,408,832,000 -- más de cien billones (en la
escala larga, un trillón corto). Con veinticinco paradas el número tiene 24
dígitos.

Y esa es la mitad *fácil*, porque supone que ya se sabe cuáles paradas lleva ese
camión. Repartir 60 paradas entre seis camiones se puede hacer de unas
5 x 10^46 maneras, antes de que nadie haya ordenado una sola.

De ahí se desprenden tres consecuencias, y explican casi todo lo que hace un
software de ruteo:

1. **Nadie encuentra la mejor respuesta.** Ni este sistema, ni los caros.
   Revisar todas las posibilidades no es lento: es físicamente imposible. Todo
   sistema real encuentra una *buena* respuesta dentro de un presupuesto de
   tiempo y se detiene.
2. **Por eso "¿qué tan buena?" es una pregunta real con un número real**, y es
   la razón por la que la §6 de este documento mide las respuestas del gateway
   contra un solver más fuerte en vez de afirmar que están bien. Un proveedor de
   ruteo que no le entregue ese número es que no lo ha medido.
3. **Cambios pequeños en la pregunta cambian mucho la respuesta.** "Usar la menor
   cantidad de camiones" y "recorrer la menor cantidad de kilómetros" son
   problemas distintos con respuestas distintas, y al sistema hay que decirle
   cuál quiso decir. Eso son los modos de objetivo de la §9.

## 0.4 Por qué la respuesta parece equivocada al principio

Los planes que produce un software de ruteo ofenden con frecuencia la intuición
de quien conoce el territorio. Por lo general el software tiene razón, y hay dos
razones medidas para ello.

**El cliente más cercano en el mapa muchas veces no es el más cercano de
manejar.** En los datos de Costa Rica de este mismo proyecto, la línea recta
subestima el recorrido real en cerca de un **40% en la mediana**. En el peor par
muestreado, dos direcciones separadas por **914 metros** estaban a **16.1 km por
carretera** -- un río, una autopista y un sistema de vías de un solo sentido de
por medio. El despachador que mira los puntos en un mapa está leyendo líneas
rectas. El planificador está leyendo calles.

**Ir y volver no son el mismo viaje.** Dos tercios de los pares de direcciones
de la muestra metropolitana tienen una distancia distinta de ida que de vuelta,
por vías de un solo sentido y restricciones de giro. Una ruta que se ve
desperdiciada en un sentido puede ser el sentido barato.

Hay una tercera razón que no tiene nada que ver con la geografía: **las mismas
paradas pueden producir un día muy distinto según cómo se agrupen.** Seis
paradas reales de San José, en dos racimos compactos, costaron **95.03 km**
cuando se entregaron alternando entre los racimos, y **49.68 km** cuando se
entregaron agrupadas. Paradas idénticas, depósito idéntico -- casi el doble de
manejo. Agrupar es una decisión, y es una que el software tiene que acertar.

## 0.5 Las palabras que se usan

| Palabra | Significa |
|---|---|
| **Depósito** | Donde arrancan los vehículos, y normalmente donde terminan. Una bodega, un patio, una farmacia |
| **Parada** / **orden** / **trabajo** | Un lugar donde tiene que ocurrir algo. Una entrega, una recolección, una visita técnica |
| **Ruta** | El día completo de un vehículo: depósito, paradas en orden, regreso al depósito |
| **Flota** | Los vehículos disponibles, que pueden diferir entre sí |
| **Capacidad** | Lo que un vehículo puede llevar. Kilogramos, tarimas, cajas, asientos -- con frecuencia varias a la vez |
| **Ventana de tiempo** | Cuándo se puede atender una parada. "Entre 9 y 12", "después de que abra el local" |
| **Tiempo de servicio** | Cuánto dura la parada en sí, aparte del manejo para llegar |
| **Jornada** | Cuándo puede trabajar el conductor, incluyendo descansos y límites legales de conducción |
| **Factible** | Un plan que no rompe ninguna regla dura. Lo primero que hay que acertar |
| **Óptimo** | El plan más barato posible. Nadie lo tiene; la meta honesta es *bueno, y demostrablemente factible* |

La distinción de las dos últimas filas es la columna vertebral del diseño de
este sistema: **la factibilidad es una compuerta y la optimalidad es una meta.**
Un plan que ahorra 8% y manda a un conductor donde un cliente que está cerrado
no es un ahorro del 8%: es una entrega fallida y una llamada telefónica.

## 0.6 Cómo se ve el ruteo en el mundo real

Cada fila es un tipo de operación real, y la columna del medio es lo que la hace
difícil -- que es lo que decide si el endpoint `/vrp` desplegado puede
atenderla, o si hace falta la plataforma de la Parte II.

| Operación | Qué la restringe | Atendida por |
|---|---|---|
| **Paquetería** -- mensajería, 600 entregas desde un centro | La cantidad de paradas; los camiones se llenan | El `/vrp` del gateway resuelve esto |
| **Reabastecimiento de supermercados** -- tarimas del centro de distribución a las tiendas | Peso *y* cantidad de tarimas a la vez; espacios de andén | Plataforma -- capacidad multidimensional |
| **Reparación de electrodomésticos** -- técnicos visitando casas | La cita del cliente; el trabajo dura 40 minutos; solo algunos técnicos están certificados en gas | Plataforma -- ventanas, tiempo de servicio, competencias |
| **Recolección de residuos** -- una tolva que se llena | El camión debe descargar en el relleno a media jornada y volver | Plataforma -- viajes múltiples y recarga |
| **Atención domiciliaria de salud** -- enfermeras visitando pacientes | Ventanas de visita, credenciales, y que la misma enfermera atienda al mismo paciente | Plataforma -- ventanas, competencias, consistencia |
| **Transporte escolar** | Asientos, y cuánto tiempo puede ir un menor a bordo | Plataforma -- capacidad y límites de tiempo a bordo |
| **Distribución de combustible o GLP** | Compartimentos, pesos por eje, restricciones de acceso al sitio | Plataforma -- compartimentos, clases de acceso |
| **Fuerza de ventas / mercadeo en punto** | Cada tienda visitada dos veces por semana, no solo una vez esta semana | Plataforma -- horizonte multiperiodo |
| **Mensajería del mismo día** | Los trabajos llegan *durante* el día, después de hecho el plan | Plataforma -- oleadas de despacho, reoptimización |
| **Supermercado en línea** | El cliente escogió una franja de dos horas al pagar | Plataforma -- ventanas de tiempo duras |
| **Mensajería de documentos firmados** | Diez minutos parado en cada puerta; el manejo casi no importa | Plataforma -- domina el tiempo de servicio |

Léase esa última columna y la forma de este repositorio queda clara: **el
endpoint desplegado atiende bien la primera fila y las demás no**, que es
exactamente la razón de existir de la plataforma.

## 0.7 Qué tiene que decidir realmente un planificador

Antes de que cualquier software pueda ayudar, alguien tiene que responder tres
preguntas de negocio. No son técnicas, y equivocarse en ellas es la causa
habitual de un plan en el que nadie confía.

1. **¿Qué nunca debe pasar?** ¿Una cita incumplida? ¿Un camión sobrecargado? ¿Un
   conductor pasado de sus horas? Esto se convierte en *restricciones duras*, y
   un plan que rompe una se descarta por barato que sea.
2. **¿Qué estamos tratando de minimizar?** Kilómetros, horas, vehículos, costo o
   retrasos. Estas se jalan entre sí -- el plan con menos camiones casi nunca es
   el plan con menos kilómetros.
3. **¿Qué preferiríamos, en igualdad de condiciones?** Cargas de trabajo
   balanceadas, el mismo conductor en la misma calle todos los días, territorios
   compactos. Esto es valor real y es lo primero que se sacrifica cuando el día
   viene apretado.

Este sistema codifica ese orden de forma literal: primero las reglas duras,
segundo lo que se está minimizando, al final las preferencias, y ninguna
cantidad de lo tercero compra jamás nada de lo primero. La §9 es esa estructura
escrita.

---

# Parte I -- El `/vrp` del gateway

## 1. Para qué sirve

Usted tiene unos depósitos y un montón de paradas. Quiere saber cuál depósito
atiende cuál parada, y en qué orden debe manejar cada vehículo. No necesita
modelar cuándo está el cliente en casa, cuánto dura la entrega, ni cuánto pesan
los paquetes.

Ese es todo el producto. Es un servicio de *territorio y secuencia*, y dentro de
esos límites es rápido, cacheado, acotado y honesto al rechazar lo que no puede
hacer.

## 2. La solicitud, campo por campo

```json
{
  "depots": [{"id": "D1", "longitude": -84.09, "latitude": 9.93}],
  "stops":  [{"id": "S1", "longitude": -84.10, "latitude": 9.94},
             {"id": "S2", "longitude": -84.14, "latitude": 9.96}],
  "capacity": 35,
  "clustering_mode": "travel_time",
  "hysteresis_m": 2000.0,
  "max_radius_km": 25,
  "roundtrip": true
}
```

| Campo | Por defecto | Qué hace **realmente** |
|---|---|---|
| `depots` | obligatorio, 1--500 | Orígenes. Cada uno puede llevar un `id`, que se convierte en la etiqueta del vehículo |
| `stops` | obligatorio, 1..`VRP_MAX_STOPS` (2000) | El trabajo. Cada una puede llevar un `id`, que se devuelve en la ruta |
| `capacity` | 35 | **Máximo de paradas en un vehículo.** No es peso, ni volumen, ni unidades. Es el tamaño del bloque |
| `vehicle_count` | ninguno | **El solver nunca lo lee.** Se valida (`> 0`) únicamente para que un cliente viejo que mande `0` siga recibiendo su 422 |
| `clustering_mode` | `travel_time` | `travel_time`, `distance` o `radial` -- cuál costo decide el depósito |
| `hysteresis_m` | 2000.0 | Cuánto mejor debe ser un depósito no obvio antes de que una parada se mueva a él |
| `max_radius_km` | ninguno | Más allá de esta distancia por carretera desde su depósito, una parada se reporta inalcanzable en vez de atenderse |
| `roundtrip` | true | Si el vehículo regresa al depósito |

Dos de esas filas sorprenden a casi todo el mundo. **`capacity` es un conteo de
paradas**, así que `"capacity": 35` significa "ningún vehículo recibe más de 35
entregas" y no dice nada sobre lo que va dentro del camión. Y **`vehicle_count`
no hace absolutamente nada** -- la cantidad de vehículos es una *salida*,
derivada de en cuántos bloques se dividen las paradas.

## 3. El algoritmo, en cuatro fases

```
  paradas + depósitos
        |
   [1]  |  POST /table  ->  matrices de duración y distancia, depósitos x paradas
        v
   [2]  asignar: un depósito por parada          (gateway/src/vrp/allocate.rs)
        |    ancla -> cordura -> inalcanzable -> histéresis
        v
   [3]  barrido + bloques: ordenar por rumbo, cortar en cargas de vehículo
        |                                          (gateway/src/vrp/solve.rs)
        v
   [4]  por bloque: POST /trip  ->  OSRM resuelve el TSP de ese vehículo
        |    concurrencia acotada por VRP_CHUNK_CONCURRENCY
        v
  rutas + totales
```

### Fase 1 -- la matriz de costos

Una sola llamada a `/table` entrega una matriz de depósitos x paradas con
duraciones y distancias. OSRM reporta un par sin ruta como `null`; el gateway
sustituye un centinela de `1e12` para que las comparaciones de abajo tengan un
número con qué trabajar. Un par que lleva el centinela es un par sin conexión
vial, y eso se rastrea, no se oculta.

### Fase 2 -- asignación: cuál depósito atiende esta parada

Esta es la parte con opiniones de verdad. Para cada parada, en orden:

1. **Ancla.** El depósito más cercano en línea recta. Distancia
   equirrectangular con una sola escala de longitud tomada del centroide de
   todas las paradas -- calculada una vez por solicitud, no por parada.
2. **El modo radial se detiene aquí.** Nunca consulta la matriz vial.
3. **Mejor.** El depósito más barato en la matriz elegida -- duraciones para
   `travel_time`, distancias para `distance`.
4. **Cordura visual.** Si el "mejor" está más de `VRP_SANITY_LIMIT_M` (50 km)
   *más lejos en línea recta* que el ancla, se toma el ancla de todos modos.
   Esto existe para impedir que una sola autopista rápida haga que un depósito
   lejano parezca "mejor" de una forma que ningún despachador aceptaría.
5. **Manejo de inalcanzables.** Si el "mejor" lleva el centinela, se toma el
   ancla. Si el ancla lo lleva, se toma el "mejor".
6. **Histéresis.** Solo se abandona el ancla si el "mejor" le gana por más que
   la banda: `best_cost < anchor_cost - hysteresis`. Si no, se queda en el ancla.

Después la parada se rechaza como inalcanzable si la distancia vial desde el
depósito elegido lleva el centinela (nada puede rutear hasta ella) o si excede
`max_radius_km`. Todo lo demás se asigna.

> **El orden de esas verificaciones es el algoritmo.** Cambiarlo cambia los
> resultados. Los empates van al índice de depósito más bajo, que es lo que hace
> que la misma entrada produzca el mismo plan todas las veces.

**Cuánto vale la histéresis, medido.** Con su valor por defecto retiene **2 de
400 paradas** con separación nacional entre depósitos y **17 de 400** con
separación urbana (`docs/whitepapers/02` §4). Su efecto es función de la
geometría de los depósitos, no del número. No la ajuste en abstracto --
mídala sobre sus propios depósitos.

> Una corrección que conviene cargar: el §3.6 de `SDD.md` describe la histéresis
> como algo que mantiene estables los territorios *entre corridas*. No puede. No
> existe una asignación previa en una solicitud `/vrp`; la banda compara un
> ancla por distancia aérea contra el mejor por costo vial **dentro de una misma
> corrida**.

### Fase 3 -- orden de barrido y bloques

Las paradas de un depósito se ordenan por **rumbo** alrededor del depósito, con
la longitud escalada por `cos(latitud)` para que un rumbo sea un rumbo y no uno
estirado por la proyección. Los bloques son entonces cortes contiguos de ese
orden, de modo que cada vehículo recibe una cuña del territorio del depósito en
vez de un pedazo arbitrario de la entrada.

Esto no es cosmético. Seis paradas en dos racimos compactos costaron **95.03 km
entregadas alternando y 49.68 km entregadas agrupadas** -- las mismas paradas,
el mismo depósito, y la única diferencia es el orden en que llegaron. Ordenar
por rumbo hace que la respuesta dependa de dónde están las paradas.

El barrido luego **rota para comenzar después de la cuña vacía más ancha**. El
corte de `atan2` en +/-pi es un artefacto del sistema de coordenadas, no de las
paradas: un racimo ubicado justo al oeste del depósito tiene miembros a ambos
lados de ese corte, y cortar ahí parte ese racimo entre dos vehículos --
exactamente lo que el barrido existe para evitar. Seis paradas de San José se
mantuvieron en 94.56 km hasta que se movió el corte, contra 49.68 km una vez
movido.

El tamaño de bloque es `min(VRP_CHUNK_SIZE, capacity, 199)`, con piso en 1. El
199 es el límite de 200 coordenadas del `/trip` de OSRM menos el depósito.

**Un bloque único nunca se barre.** Un solo bloque no tiene membresía que
decidir, y reordenarlo cambiaría la URL de `/trip` aguas arriba, su llave de
caché y su fixture de paridad, sin cambiar la ruta.

### Fase 4 -- el TSP por vehículo

Cada bloque se convierte en una llamada a `/trip` con el depósito más sus
paradas. **El gateway no resuelve el TSP.** Lo resuelve OSRM, y el gateway mapea
el orden optimizado de waypoints de vuelta a los índices de parada del
solicitante, descarta el waypoint 0 (el depósito) y retransmite la geometría sin
alterar sus bytes.

Los bloques corren concurrentemente, acotados por `VRP_CHUNK_CONCURRENCY` (4 por
defecto), de modo que una sola resolución no puede saturar el motor. La primera
falla aborta a sus hermanas en vez de dejarlas corriendo por una respuesta que
nadie va a leer.

## 4. La respuesta

```json
{
  "code": "Ok",
  "routes": [{
    "vehicle_id": "D1-1",
    "depot_index": 0,
    "stops_indices": [0, 1],
    "stop_ids": ["S1", "S2"],
    "stop_coordinates": [...],
    "route_geometry": {"type": "LineString", "coordinates": [...]},
    "distance_meters": 12450.0,
    "duration_seconds": 920.0
  }],
  "total_distance": 12450.0,
  "total_duration": 920.0
}
```

**Etiquetas de vehículo.** Un depósito con `id` que necesita un vehículo se
etiqueta con ese id. Uno que necesita varios recibe `<id>-1`, `<id>-2`. Un
depósito sin id cae a un entero corrido a lo largo de toda la respuesta.

**`/vrp` no lleva campo `unreachable_stops`.** Es una decisión deliberada de
compatibilidad: el modelo de respuesta de su predecesor en FastAPI lo eliminaba,
así que los clientes nunca lo vieron. Use **`POST /vrp/allocate`** para verlas:
el mismo cuerpo de solicitud, devolviendo las asignaciones depósito-a-paradas y
la lista de inalcanzables, sin ejecutar ningún TSP. Es el endpoint correcto para
"revisar que los territorios se vean sensatos antes de comprometerse con un
plan".

## 5. Lo que no hace

Dicho sin rodeos, porque cada uno de estos puntos es una pregunta que alguien va
a hacer:

| No soportado | Consecuencia |
|---|---|
| Ventanas de entrega | Un plan no puede saber que el cliente no está entre 12 y 2 |
| Duración de servicio por parada | Una firma de diez minutos y una entrega en la acera cuestan lo mismo: nada |
| Jornadas, descansos, horas de conducción | El largo de la ruta se acota por cantidad de paradas, no por una jornada laboral |
| Carga, peso, volumen, compartimentos | `capacity` cuenta paradas; una tarima y un sobre son idénticos |
| Competencias, clases de acceso, incompatibilidad | Cualquier vehículo puede atender cualquier parada |
| Tráfico según la hora | Todo plan asume velocidad de flujo libre. No hay hora de salida |
| Prioridades, premios, órdenes descartables | Toda parada se atiende o se reporta inalcanzable; nada se *elige* en contra de nada |
| Recolecciones emparejadas con entregas | Las paradas son independientes; no hay precedencia |
| Flotas heterogéneas | Un solo tamaño de bloque para todos |
| Viajes múltiples / recarga | Un vehículo hace un viaje |

También vale decirlo: **no hay optimización de la cantidad de vehículos.** El
número de vehículos sale de `ceil(paradas_en_el_depósito / capacity)`. Si lo que
quiere es la flota más barata, esa es otra pregunta y otra herramienta.

## 6. ¿Qué tan buenas son las respuestas?

Medido contra PyVRP sobre la instancia y la flota idénticas -- 60 paradas del
GAM, un depósito, 2,000 iteraciones, semilla 0 (`docs/whitepapers/03` §3):

| Flota | Gateway | PyVRP | Brecha |
|---|---|---|---|
| 1 vehículo (cap. 60) | 341,105 m | 315,173 m | **+8.2%** |
| 3 vehículos | -- | -- | **+11.7%** |
| 6 vehículos (cap. 10) | 481,715 m | 419,593 m | **+14.8%** |

Importan dos lecturas. **La brecha con un vehículo aísla la secuenciación** --
con un vehículo no hay partición que equivocar, así que ese +8.2% es lo que el
`/trip` de OSRM cede contra un solver real en puro ordenamiento. **La brecha
crece con la flota**, porque con seis vehículos también se está juzgando la
partición por barrido y bloques, que es una partición heurística.

Y el resultado silencioso: el `total_distance` que reporta el gateway y el
evaluador canónico en Python, recalculando desde la misma matriz, **coinciden
dentro de 0.9 m en planes de 341 a 482 km**. Eso es redondeo decimal. La
aritmética del gateway no es donde se va ese 14.8%.

**El cómputo es la otra mitad del intercambio.** Sobre la misma instancia con
tres vehículos, PyVRP llega a −10.5% en 3,200 iteraciones y 560.9 ms -- pero a
un tiempo de reloj comparable, cerca de 12 ms, *ya va 7% adelante*. La ventaja
de velocidad del gateway es menor de lo que aparenta.

## 7. Límites operativos

| Límite | Por defecto | Comportamiento al cruzarlo |
|---|---|---|
| `VRP_MAX_STOPS` | 2000 | `422` nombrando el límite |
| `VRP_MAX_CONCURRENCY` | 1 por worker | Cola, luego `503` con `Retry-After` tras `VRP_QUEUE_TIMEOUT` |
| `VRP_MAX_QUEUE_DEPTH` | 0 (desactivado) | Rechaza de inmediato en vez de hacer esperar al solicitante para negarle |
| `VRP_CHUNK_SIZE` | 80 | Topa las paradas por vehículo sin importar `capacity` |
| `VRP_CHUNK_CONCURRENCY` | 4 | Llamadas `/trip` concurrentes dentro de una resolución |
| `MATRIX_MAX_CELLS` | 10000 | La matriz depósitos x paradas debe caber |
| Límite de tasa | `100/minute` | `429` |

**La memoria pico es paradas x resoluciones concurrentes.** Una resolución de
2,000 paradas alcanzó 277 MB; cuatro concurrentes llegaron a 615 MB en un host
de 2 GB. La concurrencia a nivel de nodo es `workers x VRP_MAX_CONCURRENCY` --
suba ambas juntas, contra un techo medido.

---

# Parte II -- La plataforma `vrp/`

Todo lo que la Parte I dijo que el gateway no puede hacer, esto sí. Es una
biblioteca en Python construida contra `docs/vrp-spec-driven-development.md`,
una especificación con una constitución, un catálogo de
requisitos, un conjunto de invariantes y un backlog ordenado. **84 de sus 86
tareas están hechas**, y las dos que faltan están bloqueadas por el mundo
exterior y no por esfuerzo: `T-84` (compilar un problema hacia NVIDIA cuOpt)
espera una máquina con tarjeta, y `T-99` (fixtures de producción `P1`/`P2`)
espera a que una operación real aporte registros de entrega.

Es una biblioteca. Nada la expone como servicio. `vrp/api.py` implementa el
contrato de solicitud y respuesta de `/verify` precisamente porque ese endpoint
no necesita solver alguno -- es una función pura de (problema, plan) a reporte
-- "lista para el proceso que termine hospedándola".

## 8. El modelo de dominio

Independiente del solver por construcción: nada en `vrp/model.py` sabe cómo se
produce una ruta, solo cómo se ve una legal.

| Entidad | Lleva |
|---|---|
| `Location` | id, lat/lon, índice de matriz, sobrecarga de permanencia, capacidad de andén, inventario, clases de acceso, peso máximo de vehículo |
| `TimeWindow` | inicio, fin, dureza (`HARD`/`SOFT`), costo por segundo de adelanto y de atraso |
| `StopSpec` | un extremo de una orden: ubicación, ventanas de tiempo **múltiples**, tiempo de servicio fijo, tiempo de servicio por unidad |
| `Order` | id, tipo, cantidades multidimensionales, `StopSpec` de recolección y/o entrega, nivel de prioridad, premio, hora de liberación, competencias requeridas, tiempo máximo a bordo, clase de orden, incompatibilidades |
| `Vehicle` | capacidades **por dimensión**, ventana de jornada, ubicaciones de inicio y fin, duración y distancia máximas, competencias, costo fijo, costo por metro/segundo/orden, tarifa de tiempo extra, perfil, factor de servicio, ubicaciones y límites de recarga, batería y curva de carga, clase de acceso, peso bruto, indicador de ruta abierta, reglas de horas de conducción, estado inicial del conductor |
| `TravelMatrix` | duraciones y distancias fijadas, un hash de `version`, y un marcador `degraded` |
| `Lock` | una instrucción del operador que el plan debe honrar exactamente |
| `Synchronisation` | dos rutas obligadas a encontrarse, con brecha mínima y máxima |
| `Problem` / `Solution` | la instancia completa, y un plan sobre ella |

Nótese qué está en plural: **capacidades** es un diccionario de dimensiones, así
que kilogramos, tarimas y cajas quedan restringidos a la vez. **Ventanas de
tiempo** es una tupla, así que "9--12 o 14--17" es una sola orden y no dos. Todo
es entero -- dinero en unidades menores, tiempo en segundos, distancia en metros
-- porque la acumulación de punto flotante a lo largo de una línea de tiempo de
200 paradas es la forma de terminar con un evaluador y un verificador que no
coinciden.

## 9. El objetivo es una jerarquía, no una suma ponderada

La especificación es tajante sobre el porqué: las sumas ponderadas ingenuas son
"el error de modelado más común en ruteo de producción", porque los pesos que
equilibran bien en un día de 200 paradas se invierten en silencio en uno de
2,000.

```
Nivel 0  Violaciones de restricciones duras   (cero en una solución FACTIBLE)
Nivel 1  Órdenes de prioridad 0 sin atender
Nivel 2  Órdenes sin atender por nivel de prioridad descendente
Nivel 3  Costo de flota: costo fijo de los vehículos desplegados
Nivel 4  Costo de operación: distancia + duración + tiempo extra
Nivel 5  Violaciones blandas: adelanto, atraso, capacidad blanda
Nivel 6  Desempates: balance de carga, consistencia, compacidad
```

Los pesos de cada nivel se **derivan de la instancia**, no están fijos en el
código, y cada uno se escoge para dominar estrictamente el valor máximo
alcanzable de todos los niveles inferiores.

Cinco modos cambian *cuáles niveles comparten un escalón*, nunca su orden:

| Modo | Escalones | Para |
|---|---|---|
| `MIN_VEHICLES` | `T2 > T3 > T4` | Días con flota restringida, planificación de capacidad |
| `MIN_COST` | `T2 > T3+T4` | Operación normal -- se despliega un vehículo si y solo si su costo fijo se paga |
| `MIN_DURATION` | como `MIN_COST`, solo tiempo | Operaciones restringidas por horas de conductor |
| `MAX_SERVICE` | como `MIN_COST`, las órdenes siguen siendo **obligatorias** | Días pico, protección de SLA |
| `PRIZE_COLLECTING` | `T1 > T2+T3+T4` | Escasez de capacidad y modelos de mercado |

Las dos filas que hay que leer dos veces son las últimas. Una implementación que
trate todos los modos como estrictamente lexicográficos pasa la mayoría de las
pruebas y aun así está mal en ambos casos: `MIN_COST` colapsa en `MIN_VEHICLES`
porque un vehículo menos siempre gana, y `PRIZE_COLLECTING` nunca puede
descartar nada.

## 10. Dieciséis invariantes y un verificador independiente

Esta es la parte de la plataforma que más la distingue, y viene directo del
primer principio de la constitución:

> **CON-1 -- La factibilidad no es negociable; la optimalidad sí.** Un plan que
> viola una restricción dura no vale nada por barato que sea. El sistema nunca
> debe emitir un plan declarado factible sin que pase un verificador
> independiente que no comparta código con el solver.

| | Verifica |
|---|---|
| `INV-1` | Cada orden aparece exactamente una vez entre rutas y no asignadas |
| `INV-2` | La recolección y la entrega de un envío van en la misma ruta, recolección primero |
| `INV-3` | Por paso: llegada <= inicio de servicio; inicio + servicio = salida |
| `INV-4` | La llegada encadena correctamente a través de la versión **fijada** de la matriz |
| `INV-5` | La carga se mantiene dentro de capacidad en cada dimensión y en cada paso |
| `INV-6` | Se respetan duración, distancia y los límites de jornada de la ruta |
| `INV-7` | La línea de tiempo de conducción satisface el conjunto de reglas activo |
| `INV-8` | Cada lock se satisface exactamente |
| `INV-9` | El objetivo recalculado desde las rutas iguala al objetivo reportado |
| `INV-10` | Ninguna ruta lleva una orden cuyas competencias, clase o acceso le falten al vehículo |
| `INV-11` | Las recargas ocurren solo donde está permitido, y no más seguido de lo permitido |
| `INV-12` | Ningún depósito despacha más vehículos en una franja que los andenes que tiene |
| `INV-13` | Ningún depósito entrega más de lo que tiene, contado **globalmente** |
| `INV-14` | Ningún envío va a bordo más tiempo que su máximo |
| `INV-15` | Las rutas acopladas efectivamente se encuentran como lo exige su sincronización |
| `INV-16` | Un vehículo eléctrico nunca llega pasado de vacío, y carga solo en sus propios cargadores |

**A `INV-9` se le llama la prueba más valiosa del sistema.** La mayoría de los
errores silenciosos de optimización son un evaluador que se contradice a sí
mismo; recalcular el objetivo desde el plan los atrapa.

**`INV-10`--`INV-16` están numerados más allá de los nueve originales
deliberadamente.** Cada uno se agregó cuando una restricción real resultó no
tener ninguna invariante vigilándola.

**¿De verdad atrapa cosas el verificador?** Medido en vez de afirmado:
`experiments/e06_mutation.py` construye un plan verificado con distancias viales
reales y le siembra seis defectos. **Seis de seis atrapados, cada uno nombrando
la invariante correcta.** El plan limpio pasa.

El evaluador (`vrp/evaluator.py`, 504 líneas) y el verificador
(`vrp/verify/verifier.py`, 806 líneas) están separados a propósito: el
verificador no debe importar el evaluador que se usa dentro de la búsqueda
local, y debe escribirlo un autor distinto. Las discrepancias entre los dos se
tratan como defectos P1.

## 11. El catálogo de restricciones

Lo que la plataforma puede expresar y el gateway no:

| Área | Módulo | Contenido |
|---|---|---|
| Capacidad | `model` | Múltiples dimensiones simultáneas; carga pico, no total |
| Ventanas de tiempo | `model` | Varias ventanas disjuntas por parada; duras o blandas con penalización por segundo |
| Tiempo de servicio | `model`, `calibrate` | Fijo más por unidad; ajustado desde telemetría |
| Viaje dependiente de la hora | `timedependent`, `speedfit` | Velocidad constante por tramos, por clase de arco y franja horaria, preservando FIFO |
| Horas de conducción | `hos/` | Un motor de reglas, no un tope de duración |
| Competencias y acceso | `model`, `diagnose` | Competencias requeridas, incompatibilidad por clase de orden, clases de acceso al sitio, límites de peso del vehículo |
| Locks | `locks`, `triggers` | Instrucciones del operador como restricciones duras; los conjuntos infactibles se devuelven como **conjunto mínimo en conflicto** |
| Consistencia | `consistency` | El mismo conductor, el mismo territorio, día tras día -- tratado como valor, no como concesión |
| Viajes múltiples | `model` | Ubicaciones de recarga, cantidad de recargas, duración de recarga |
| Sincronización | `synchronise` | Dos rutas encontrándose, con brecha mínima y máxima |
| Inventario de depósito | `depots` | Existencias globales, contadas en todas las rutas que se surten de él |
| Vehículos eléctricos | `electric`, `battery` | Autonomía, ubicación de cargadores, curvas de carga con estrechamiento |
| Premios y prioridad | `pcdispatch`, `model` | Niveles de prioridad, premios, órdenes descartables frente a obligatorias |

## 12. Solvers, y por qué hay varios

| Capa | Módulo | Rol |
|---|---|---|
| Portafolio | `portfolio` | Corre varios motores y los califica con un único objetivo canónico |
| Adaptadores | `solve/pyvrp_adapter.py`, `solve/ortools_adapter.py` | Núcleos maduros preferidos sobre los hechos a la medida -- constitución `CON-10` |
| Ruina y reconstrucción | `lns` | SISR: quitar trabajo relacionado y reconstruirlo mejor |
| Búsqueda local | `localsearch` | Evaluación de movimiento en O(1), el mayor determinante del rendimiento de la búsqueda local |
| Partición de conjuntos | `setpartition` | Recolecta cada ruta distinta generada y resuelve una cobertura exacta sobre el conjunto |
| Pulido | `polish` | Pasadas exactas por ruta, después de agotado el presupuesto de la metaheurística |
| Descomposición | `decompose` | Particiona instancias enormes, reoptimiza subproblemas contra un incumbente y repara las costuras |

Vale citar el punto arquitectónico de la especificación: **el solver es la parte
pequeña.** De las ocho capas, las que guardan valor duradero son el modelo de
dominio, el subsistema de matrices, el evaluador, el verificador y el ciclo de
calibración. La capa de solvers es explícitamente "reemplazable".

## 13. Operación dinámica -- el día después del plan

Un plan se topa con la realidad en la primera hora. Esta es la maquinaria para
eso:

| Módulo | Responde |
|---|---|
| `epochs` | Oleadas de despacho: qué debe salir ya y qué puede esperar a la siguiente |
| `policies` | Bases de comparación greedy, lazy y aleatoria contra las cuales medir una política real |
| `pcdispatch` | Cada época como un VRPTW con recolección de premios -- el premio codifica cuánto queremos despacharla ahora |
| `icd` | Muestrea demanda futura, resuelve cada escenario y despacha aquello en lo que la mayoría coincide |
| `committed` | Lo que ya se ejecutó y nunca puede replanificarse |
| `triggers` | Reoptimización con locks: una avería a las 11:00 replanifica el trabajo afectado y nada más |
| `stability` | Churn -- medirlo, ponerle precio y decidir cuánto pagar por un plan estable |
| `quote` | El precio de insertar o quitar una orden, sin replanificar |
| `replay` | Reproduce días históricos época por época para evaluar una política sin riesgo |

## 14. Aprender de lo que de verdad pasó

Constitución `CON-6`: confíe en el plan solo hasta donde sobreviva el contacto
con la realidad. La calidad del plan se mide contra trazas GPS ejecutadas, no
contra la propia estimación del plan.

| Módulo | Ajusta |
|---|---|
| `adherence` | Ingesta de telemetría; dónde divergieron el plan y el día |
| `calibrate` | Duración de servicio en función de arquetipo, cantidad, vehículo y hora del día |
| `speedfit` | Multiplicadores de velocidad por clase de arco contra el supuesto de flujo libre del motor |
| `zones` | El prior de secuencia de zonas aprendido de las rondas que los conductores realmente manejaron |

## 15. Operar un planificador de forma responsable

| Módulo | Provee |
|---|---|
| `snapshot` | Entradas, configuración y salida inmutables; un plan reproducible desde su snapshot |
| `observe` | El registro de corrida: trayectoria del objetivo, marcas de incumbente, conteo de violaciones, tasa de aciertos de caché, semilla |
| `explain` | Por orden: por qué este vehículo, esta posición, esta hora -- y qué haría falta para cambiarlo |
| `diagnose` | Infactibilidad previa al vuelo, por una pasada diagnóstica explícita en vez de por inferencia |
| `rollout` | Modo sombra y despliegue canario |
| `anonymise` | La compuerta que pasa cualquier corpus derivado de entregas reales antes de salir del perímetro |
| `modelcheck` | La compuerta que pasa un modelo de entrega configurado en JSON antes de publicarse |
| `benchmarks` | Lectura de instancias públicas -- CVRPLIB, Solomon, Li & Lim |

---

# Parte III -- Casos de uso y escenarios

## 16. Cuál herramienta para cuál pregunta

| Pregunta de negocio | Use | Por qué |
|---|---|---|
| "¿Cuál bodega debe atender a este cliente?" | `POST /vrp/allocate` | Territorios sin pagar por un TSP |
| "¿En qué orden debe manejar este conductor hoy?" | `POST /vrp` o `POST /trip` | La secuenciación es la fortaleza del gateway |
| "Repartir 600 entregas entre 4 depósitos y 20 camiones" | `POST /vrp` | Asignación más bloques es exactamente esto |
| "Rutear alrededor de la ventana de 2 a 5 del cliente" | La plataforma `vrp/` | El gateway no tiene reloj |
| "Esta entrega toma 20 minutos, aquella 2" | La plataforma `vrp/` | El tiempo de servicio es un campo del modelo de dominio |
| "El camión lleva 800 kg y 12 tarimas" | La plataforma `vrp/` | Capacidad multidimensional |
| "Solo técnicos certificados pueden hacer este trabajo" | La plataforma `vrp/` | Competencias y clases de acceso |
| "Se averió un camión a las 11:00" | La plataforma `vrp/` -- `committed` + `triggers` | Replanificar lo afectado, congelar lo demás |
| "¿Cuántos camiones necesitamos el próximo trimestre?" | La plataforma `vrp/` -- `scenarios`, `fleet` | Dimensionamiento de flota sobre un conjunto de escenarios |
| "¿Por qué quedó esta orden sin asignar?" | La plataforma `vrp/` -- `explain`, `diagnose` | La explicabilidad es un requisito de producto (`CON-5`) |
| "¿Este plan, de cualquier sistema, es legal?" | `vrp/api.py` `/verify` | Deliberadamente público e independiente del solver |
| "¿Nuestro planificador está empeorando?" | La plataforma `vrp/` -- `adherence`, `rollout` | Medido contra GPS, no contra sí mismo |

## 17. Cinco escenarios resueltos

### 17.1 Ronda de paquetería metropolitana -- el terreno propio del gateway

600 entregas, cuatro depósitos alrededor del GAM, camiones que cargan unas 40
paradas por jornada. Sin ventanas; al cliente le llega un SMS cuando el
conductor va cerca.

```bash
curl -s localhost:8000/v1/vrp -H 'content-type: application/json' -d '{
  "depots": [ ...4 depósitos con id... ],
  "stops":  [ ...600 paradas con id... ],
  "capacity": 40,
  "clustering_mode": "travel_time",
  "max_radius_km": 30
}'
```

Obtiene unas 15 o 16 rutas etiquetadas `D1-1`, `D1-2`, ..., cada una con una
secuencia optimizada, una geometría para dibujar, y su propia distancia y
duración. Todo lo que quede fuera de 30 km de todos los depósitos regresa desde
`/vrp/allocate` como inalcanzable, en vez de quedar pegado calladamente a una
ruta.

**Revise primero, comprométase después.** Corra `/vrp/allocate` con el mismo
cuerpo y mire los territorios antes de ejecutar la resolución completa. Es más
barato y es ahí donde afloran los problemas de datos.

### 17.2 Documentos firmados -- donde el gateway es la herramienta equivocada

Un mensajero entrega un sobre, espera mientras el cliente firma, y sigue. Diez
minutos por parada, todas las paradas. El sobre pesa 200 g y regresa, así que el
maletín nunca cambia de tamaño.

`examples/src/fleet/tw/envelope_round.py` mide esta ronda y la división no está
ni cerca: **firmar se lleva la abrumadora mayoría del día, manejar un porcentaje
de un solo dígito**, y el resto es esperar a que las oficinas abran a las ocho y
vuelvan a abrir a la una.

Todo lo que el gateway optimiza es error de redondeo aquí. La única decisión
real es cuántos mensajeros mandar -- lo cual requiere tiempo de servicio,
horarios comerciales como ventanas disjuntas, y una dimensión de capacidad que
no sean kilogramos. Las tres son campos del modelo de dominio; ninguna es
expresable en una solicitud `/vrp`.

El ejemplo también muestra por qué los kilogramos no ayudarían: 200 g redondea a
1 kg, una sobreestimación de cinco veces, y lo mismo pasa con cada sobre, así
que la dimensión no cargaría ninguna información.

### 17.3 Un vehículo se avería a las 11:00

La historia de usuario detrás de la capa dinámica: *reoptimizar solo el trabajo
afectado y cercano, mientras todo lo ya ejecutado sigue ejecutado.*

`vrp/committed.py` guarda lo que ya ocurrió y nunca puede replanificarse.
`vrp/triggers.py` corre la reoptimización con locks y reporta el delta.
`vrp/stability.py` le pone precio al churn -- paradas movidas entre vehículos,
tiempos estimados que hay que volver a comunicar -- para que "mejor" no
signifique calladamente "doce clientes reciben una segunda llamada".

Vea `examples/src/fleet/dynamic/breakdown_at_eleven.py` y `churn_tradeoff.py`.

### 17.4 Cuántos camiones el próximo trimestre

`vrp/scenarios.py` toma un conjunto de escenarios de días de demanda históricos
o generados y recomienda la composición de flota que minimiza el costo total
esperado -- adquisición o arrendamiento, más ruteo, más el costo de los días que
no alcance a atender.

`vrp/fleet.py` es el procedimiento relacionado pero distinto para cuando la
cantidad de vehículos es *el* objetivo, mantenido aparte deliberadamente porque
minimizar flota y minimizar costo son búsquedas diferentes.

Vea `examples/src/fleet/alloc/tactical_sizing.py`, `fleet_minimisation.py` y
`fleet_mix.py`.

### 17.5 Revisar el plan de otro

`/verify` es deliberadamente público. Permite que los integradores revisen
planes producidos en otra parte, y obliga a que el verificador sea genuinamente
independiente del solver -- un verificador que solo puede revisar los planes de
su propio solver comparte los supuestos de ese solver, y en esos supuestos es
donde se esconden los errores.

El parser rechaza en vez de ayudar: no infiere una llegada faltante, no convierte
`"600"` en `600`, ni asume una ventana ausente. Ser servicial produciría un
reporte sobre un plan que el integrador no envió, y ese reporte pasaría -- lo
cual es peor que fallar.

Vea `examples/src/fleet/verify/external_plan.py`.

---

# Parte IV -- Referencia

## 18. Ejemplos ejecutables

Cada ejemplo es un cliente real. Corra el menú con `make examples`, o uno
directamente con `uv run --package osrm-api-gateway-examples examples/src/<ruta>`.

**El `/vrp` del gateway**

| Script | Muestra |
|---|---|
| `fleet/visualize_vrp.py` | Un plan resuelto dibujado sobre un mapa |
| `fleet/clustering_mode_comparison.py` | Los mismos datos en los tres modos, lado a lado |
| `fleet/hysteresis_demo.py` | Lo que la banda realmente retiene |
| `fleet/stress_test_vrp.py` | Comportamiento en los límites de capacidad |
| `clustering/run_clustering_workflow.py` | El flujo de asignar y luego rutear |
| `clustering/simple_id_example.py` | Identificadores propios de parada en todo el viaje de ida y vuelta |
| `benchmarking/compare_tsp.py` | Secuenciación contra alternativas |

**La plataforma: restricciones**

| Script | Muestra |
|---|---|
| `fleet/p0/must_work_at_v1.py` | Las catorce operaciones que deben funcionar en v1, cada una junto a la respuesta ingenua que rompe |
| `fleet/rich/multi_capacity.py` | Varias dimensiones de capacidad a la vez |
| `fleet/rich/heterogeneous_fleet.py` | Flotas mixtas |
| `fleet/rich/skills_and_access.py` | Competencias, clase de orden, acceso al sitio |
| `fleet/rich/hours_of_service.py` | Horas de conducción como motor de reglas |
| `fleet/rich/time_dependent.py`, `planning_under_congestion.py` | Viaje que depende de la hora de salida |
| `fleet/rich/multi_trip.py`, `ride_time.py`, `synchronisation.py` | Recarga, topes de tiempo a bordo, rutas acopladas |
| `fleet/rich/ev_recharging.py` | Autonomía y curvas de carga |
| `fleet/rich/locks_and_overrides.py` | La intención del operador como restricción dura |
| `fleet/rich/prizes_and_priority.py`, `priority_sources.py` | Trabajo descartable y por qué se descartó |
| `fleet/tw/multiple_windows.py`, `sla_windows.py`, `envelope_round.py` | Ventanas en tres sabores |

**La plataforma: flota y asignación**

`fleet/alloc/` -- `territories.py`, `fleet_minimisation.py`, `fleet_mix.py`,
`tactical_sizing.py`, `depot_inventory.py`

**La plataforma: operación dinámica**

`fleet/dynamic/` -- `breakdown_at_eleven.py`, `committed_state.py`,
`dispatch_waves.py`, `prize_collecting_epoch.py`, `replay_policies.py`,
`sample_scenario_policy.py`, `insertion_quote.py`, `churn_tradeoff.py`,
`preemption.py`

**La plataforma: explicación, aprendizaje, infraestructura**

`fleet/explain/` -- `why_unassigned.py`, `preflight_diagnosis.py`
`fleet/learn/` -- `service_time_calibration.py`, `speed_calibration.py`,
`zone_sequence_prior.py`, `plan_adherence.py`, `canary_rollout.py`
`fleet/infra/` -- `run_record.py`, `plan_snapshots.py`, `degraded_matrix.py`,
`decomposition_queue.py`, `portfolio_parallelism.py`, `accelerator_profile.py`

## 19. Clases de problema con nombre

Para quien hable en términos de la literatura y no de capacidades:

| Variante | Compuesta de | Conjunto de referencia |
|---|---|---|
| **TSP** | Un vehículo, sin capacidad ni ventanas que aprieten | Ejercitada a través de CVRP |
| **CVRP** | Capacidad, flota, depósito | CVRPLIB / Uchoa |
| **VRPTW** | CVRP más ventanas, duración de servicio, ventanas de jornada | Solomon, Gehring & Homberger |
| **MDHVRPTW** | VRPTW más capacidad, costo y perfil por vehículo, múltiples depósitos | Cordeau MDVRPTW |
| **PDPTW** | VRPTW más envíos con precedencia y mismo vehículo | Li & Lim |

**MDHVRPTW es la forma objetivo para este negocio** -- varios depósitos,
vehículos mixtos, ventanas de cliente. Cualquier cosa que trate la flota como
homogénea o el depósito como único es un peldaño, no un entregable.

**TSP no es un frente de trabajo aparte.** Un solo vehículo sin capacidad y con
ventanas ilimitadas *es* un TSP, y es la única variante ya en producción: el
`/vrp` del gateway delega la secuenciación al `/trip` de OSRM, que es un solver
de TSP.

## 20. Glosario

| Término | Aquí significa |
|---|---|
| Asignación | Asignar paradas a depósitos. La fase 2 del gateway |
| Secuenciación | Ordenar las paradas de un vehículo. La fase 4 del gateway |
| Bloque (*chunk*) | La carga de un vehículo: un corte contiguo del orden de barrido |
| Ancla | El depósito más cercano en línea recta |
| Histéresis | El margen por el que un depósito mejor por costo vial debe ganarle al ancla |
| Orden de barrido | Paradas ordenadas por rumbo alrededor de su depósito |
| Matriz fijada | Datos de viaje capturados con un hash de versión, para que un plan sea reproducible |
| Incumbente | El mejor plan encontrado hasta ahora; un solver *anytime* siempre tiene uno |
| Churn | Paradas movidas entre vehículos, y tiempos estimados que hay que volver a comunicar |
| *Must-go* | Trabajo que no puede esperar a la siguiente oleada de despacho |
| Época | Una oleada de despacho en una operación del mismo día |
| Lexicográfico | Niveles estrictamente ordenados: ninguna cantidad de un nivel inferior compra nada de uno superior |

## 21. Estado honesto, y los documentos desactualizados

- **El `/vrp` del gateway está en producción** y hace lo que describe la Parte I.
- **La plataforma tiene 84 de 86 tareas del backlog hechas**, dos bloqueadas:
  `T-84` (cuOpt) por hardware GPU, y `T-99` (fixtures de producción `P1`/`P2`)
  porque espera registros de una operación real. Ninguna está bloqueada por
  esfuerzo. Es una biblioteca; nada la expone como servicio, y cómo alcanzaría el
  gateway en Rust a un solver en Python es una pregunta arquitectónica abierta
  que la especificación no responde.
- **`vrp` ya es una distribución instalable** -- `vrp-platform` 0.3.1, construida
  con hatchling. Los solvers son extras, y `vrp/bench` queda fuera del wheel
  porque resuelve rutas contra una raíz de repositorio que una copia instalada no
  tiene por qué tener.
- **`docs/planning/VRP_SDD_FIT_GAP.md` (2026-08-25) reporta que ningún requisito
  funcional está cumplido.** Eso era cierto del `/vrp` del gateway antes de que
  existiera la mayor parte de `vrp/`, y hoy se lee como un veredicto sobre el
  sistema completo. Trátelo como un registro histórico del gateway, no como un
  estado de la plataforma.
- **El §6 de `SDD.md` dice "el VRP es solo capacidad y geografía".** Eso es
  exacto sobre el gateway e inexacto sobre la plataforma. Ambas afirmaciones de
  este documento están pensadas para leerse juntas.
- **Los conteos de configuraciones en la prosa no concuerdan** (29, 35, 36).
  `gateway/src/config.rs` es la fuente de verdad.

## 22. Qué leer después

| Para | Lea |
|---|---|
| Qué es un VRP del todo | La Parte 0 de este documento |
| Formas de solicitud y respuesta | `docs/API_REFERENCE.md` |
| Modos de agrupamiento e histéresis | `docs/features/clustering_modes.md` |
| El diseño del gateway y sus límites | `docs/SDD.md` |
| La especificación de la plataforma | `docs/vrp-spec-driven-development.md` |
| Por qué la distancia vial no es geometría | `docs/whitepapers/01-routing-a-delivery-day.md` |
| Lo que cuesta el gateway, medido | `docs/whitepapers/02-what-the-gateway-costs.md` |
| La factibilidad como compuerta, y el verificador bajo mutación | `docs/whitepapers/03-feasibility-is-a-gate.md` |
| El catálogo de escenarios | `docs/TDD/vrp-catalogue-v2.1.md` |
| Desplegarlo y operarlo | `docs/golive/PLAN_PUESTA_EN_MARCHA.md`, `docs/RUNBOOK.md` |

---

*Preparado el 2026-09-12. Las descripciones de algoritmos se leyeron de
`gateway/src/vrp/allocate.rs`, `gateway/src/vrp/solve.rs`, `gateway/src/models.rs`
y `vrp/`; las cifras medidas provienen de los experimentos de los whitepapers,
cuyos scripts y salida JSON están versionados bajo
`docs/whitepapers/experiments/`.*
