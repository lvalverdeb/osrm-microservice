# Plan de puesta en marcha: desplegar la suite y ponerla en operación

**Estado:** propuesto, 2026-09-12. Nada de esto está construido. Cada
restricción que se cita fue confirmada contra el código fuente, la configuración
o los resultados medidos de este proyecto; las duraciones son estimaciones de
planificación y son la única parte que no lo está.

> **English version:** [GO_LIVE_PLAN.md](GO_LIVE_PLAN.md). Ambos documentos se
> mantienen en paralelo; si divergen, el inglés es el que se escribió contra el
> código.

Diez semanas, nueve equipos. El despliegue en sí es un ejercicio de días: ambas
rutas están guionizadas de punta a punta con compuertas de salud, y la pila no
guarda estado persistente, así que no hay migración que diseñar ni restauración
que ensayar. Las diez semanas son casi por completo validación funcional, que es
donde está el riesgo.

## ¿Es factible?

Sí, y la respuesta gira alrededor de dos cosas que este repositorio
deliberadamente no contiene.

**El gateway no tiene autenticación ni TLS.** `gateway/src/openapi.rs` lo
declara y `the_document_declares_no_authentication` lo hace cumplir; el §5 del
`SDD.md` enumera "sin pila TLS" entre las dependencias. Nada de esto puede
exponerse a una red que usted no controle por completo hasta que TI provea un
borde que termine TLS y autentique a quien llama. Ese borde no es alcance de
este código, y trae su propia trampa: un proxy de capa 7 sin
`FORWARDED_ALLOW_IPS` colapsa a todos los clientes en una sola cubeta de límite
de tasa, y ponerlo en `*` no debilita el límite: lo anula.

**`/vrp` es solo capacidad y geografía.** Sin ventanas de tiempo, sin tiempos de
servicio, sin jornadas de conductor ni competencias, y sin dimensión temporal en
ninguna parte -- todo plan asume velocidad de flujo libre (`SDD.md` §6). La
plataforma `vrp/` en Python sí lleva esos conceptos, pero es una biblioteca sin
proceso que la exponga, y cómo alcanzaría el gateway en Rust a un solver en
Python es una pregunta de diseño abierta que `vrp/api.py` nombra en vez de
responder. Si el despacho necesita ventanas de entrega, esto es un proyecto de
desarrollo y no un despliegue. Por eso el análisis de brecha va en la semana
uno, antes de aprovisionar nada: enterarse en la semana cinco cuesta cuatro
semanas de servidores.

Todo lo demás juega a favor de la suite. Redis es un caché -- perderlo cuesta
aciertos de caché y nada más -- así que la recuperación es reconstruir y
redesplegar, y el rollback es redesplegar la compilación anterior. Quítele el
programa funcional y una instancia de staging en una red de confianza son
**cuatro a cinco días hábiles de esfuerzo de TI**.

## Qué se despliega realmente

| Componente | Origen | Notas de planificación |
|---|---|---|
| Gateway de API | compilado desde `gateway/` | lo único que este repo publica; el mismo código en ambas rutas |
| `osrm-routed` | OSRM estándar | un motor atiende un perfil, fijado cuando se construye el mapa |
| Redis | paquete o imagen estándar | caché más contadores compartidos de límite de tasa; opcional |
| Datos de mapa | extracto de Geofabrik, procesado | ~180 MB de descarga, luego extract/partition/customize |
| `vrp/` en Python | este repo, solo biblioteca | **no es un servicio**; fuera de alcance para la puesta en marcha |

Escoja la ruta de despliegue en la semana uno. Docker, salvo que el destino sea
específicamente un jail de FreeBSD -- la ruta del jail existe porque un jail no
puede correr Docker, no porque sea preferible. En la ruta del jail,
`make jail-host` no es opcional: aplica `net.inet.tcp.delayed_ack=0`, que vale
p50 de 67 ms a 9 ms, y sin él el jail simplemente parece hardware más lento.
Nada da error y nada queda en bitácora. `make jail-doctor` reporta si está
aplicado *y* persistido, así que ponga esa verificación en la lista de
aprovisionamiento y no en la memoria de alguien.

## Condiciones, restricciones y trampas

| | Hallazgo | Consecuencia | Se cierra con | Dueño |
|---|---|---|---|---|
| **bloqueante** | sin autenticación ni TLS | no puede exponerse fuera de una red de confianza | un borde construido por TI | IT-2 |
| **bloqueante** | sin ventanas de tiempo, tiempos de servicio, competencias ni jornadas | si el despacho necesita ventanas, el optimizador no puede expresarlo | análisis de brecha en la semana uno | FN-1 |
| compuerta | proxy L7 sin `FORWARDED_ALLOW_IPS` | todos los clientes comparten una cubeta; `*` lo anula | fijarlo al CIDR del proxy y comprobar cubetas por cliente | IT-2 |
| compuerta | un motor por perfil de ruteo | tres perfiles triplican tiempo de construcción, disco y RAM | confirmar la cantidad de perfiles y dimensionar contra ella | IT-1 |
| compuerta | sin alta disponibilidad en el repo | perder el host es una caída | aceptar nodo único con un RTO declarado, o financiar HA | IT-1 |
| límite | matriz topada en 10,000 celdas | matrices más anchas se rechazan; subirlo exige `--max-table-size` en **ambos** archivos de despliegue | dimensionar contra conteos reales de paradas | IT-4 |
| límite | `VRP_MAX_STOPS=2000`; una resolución así alcanzó 277 MB, cuatro concurrentes 615 MB | la memoria pico es paradas x resoluciones concurrentes | fijar los topes contra un techo de RSS medido | IT-5 |
| límite | las coordenadas viajan en la URL; ~720 migas de `/match`, techo de 24,000 bytes | trazas largas se rechazan con un 422 que nombra el límite | el cliente fragmenta por debajo del tope | IT-4 |
| límite | las métricas `process_*` leen `/proc`, ausentes en FreeBSD | paneles de CPU, RSS, descriptores y arranque quedan vacíos en el jail | construir tableros que no las necesiten | IT-5 |
| límite | `make process-osrm` necesita Docker aun apuntando al jail | una casa puramente FreeBSD necesita un host Docker, o `make jail-data` | decidir cuál | IT-3 |
| límite | `make help` anuncia `build-pkg`, `publish`, `clean-pkg` | ninguno existe; se fueron con el paquete de PyPI | anotarlo en el runbook local | IT-4 |
| límite | los documentos cuentan 29, 35 y 36 configuraciones | una revisión guiada por la prosa se salta configuraciones | revisar contra `gateway/src/config.rs` | IT-4 |

## Equipos

`IT-*` construyen y operan la plataforma; `FN-*` definen qué es un buen plan y
lo vuelven parte de la jornada. Los códigos agrupan responsabilidad; no son un
orden de trabajo.

| Código | Equipo | Es dueño de | Decide |
|---|---|---|---|
| IT-1 | Plataforma e infraestructura | hosts, sistema operativo, demonio o jail, red, DNS, cortafuegos | ruta de despliegue, dimensionamiento, postura de HA |
| IT-2 | Seguridad y acceso | TLS, autenticación, secretos, configuración de proxy de confianza | cómo prueba su identidad quien llama |
| IT-3 | Datos geoespaciales | extracto, construcción de perfiles, cadencia de refresco, ajuste a la red vial | vigencia del mapa, cuáles perfiles existen |
| IT-4 | Aplicación y liberación | compilación, configuración, ventana de deprecación de `/v1`, liberación y rollback | contenido y cadencia de liberación |
| IT-5 | SRE y observabilidad | métricas, alertas, capacidad, runbook, guardia | umbrales de alerta, topes de capacidad |
| IT-6 | Calidad y validación | suite de pruebas, compuerta de paridad, corridas de carga, evidencia de aceptación | si una compilación puede promoverse |
| FN-1 | Análisis de negocio | brecha, requisitos, indicadores, criterios de aceptación | qué se le exige al sistema |
| FN-2 | Operación de flota y despacho | depósitos, vehículos, datos maestros de paradas, uso diario, excepciones | si un plan es manejable |
| FN-3 | Integración y aplicaciones cliente | la aplicación que llama, manejo de errores, contrato | cómo se consume la API |
| FN-4 | Capacitación y gestión del cambio | procedimientos, capacitación, acompañamiento, retroalimentación | cuándo están listos los despachadores |

## Cronograma

Semanas calendario, con equipos trabajando a tiempo parcial junto a sus labores
actuales -- por eso las semanas transcurridas superan la suma de días hábiles.
La infraestructura termina temprano.

| Semana | TI | Funcional | Compuerta |
|---|---|---|---|
| 1 | IT-1 plan, IT-2 diseño del borde | **FN-1 brecha y dimensionamiento** | G0 |
| 2 | IT-1 staging, IT-2 construcción, IT-3 construcción del mapa | | |
| 3 | IT-3 validación, IT-4 despliegue y configuración | FN-2 depósitos | G1 |
| 4 | IT-2 revisión, IT-4 ajuste, IT-5 métricas, IT-6 suite | FN-2 paradas, FN-3 construcción | |
| 5 | IT-5 alertas, IT-6 aceptación | FN-2 criterios, FN-3 construcción | G2 |
| 6 | IT-5 capacidad, IT-6 carga | FN-3 rutas de error, FN-4 procedimientos | G3 |
| 7 | IT-1/2/3 producción, IT-4 liberación | **FN-2 marcha en paralelo**, FN-3 piloto, FN-4 capacitación | |
| 8 | | FN-2 marcha en paralelo, FN-4 capacitación | G4 |
| 9 | IT-4 corte, IT-5 guardia, IT-6 compuerta | FN-2 en vivo, FN-3 corte, FN-4 acompañamiento | G5 |
| 10 | IT-5 hipercuidado | FN-2 en vivo, FN-4 acompañamiento | |

## Planes por equipo

Las duraciones son días hábiles transcurridos para la actividad, no
días-persona. Donde una actividad es sobre todo tiempo de máquina -- construir
un mapa, compilar Rust en un jail de 2 GB -- hay que vigilarla, no dotarla de
personal.

### IT-1 Plataforma e infraestructura -- 9 días, semanas 1-2 y 7

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 1.1 | escoger ruta de despliegue, dimensionar el host contra perfiles, paradas pico y concurrencia | 1 | dimensionamiento FN-1 | decisión escrita, especificación de host firmada |
| 1.2 | aprovisionar staging -- host Docker en Linux, o host FreeBSD más jail | 2 | 1.1 | alcanzable, disco dimensionado para extracto más grafo |
| 1.3 | red, DNS, cortafuegos: entrada solo al borde | 1 | 1.2 | el motor inalcanzable desde fuera del host |
| 1.4 | **solo jail:** `make jail-host`, luego `make jail-doctor` | 1 | 1.2 | `delayed_ack=0` aplicado *y* persistido; Redis sin responder `DENIED` |
| 1.5 | línea base del destino con `compose-doctor` / `jail-doctor`, registrada | 0.5 | 1.3, 1.4 | arquitectura y memoria registradas antes de compilar |
| 1.6 | decidir postura de HA: nodo único con RTO declarado, o financiar dos | 1 | 1.1 | decisión registrada con la ventana de caída aceptada |
| 1.7 | aprovisionar producción replicando staging con exactitud | 2 | G2 | la salida de doctor coincide con staging |
| 1.8 | integrar parcheo, accesos y control de cambios al proceso existente | 0.5 | 1.7 | hosts en el inventario estándar |

### IT-2 Seguridad y acceso -- 12 días, semanas 1-4 y 7

Este equipo construye lo que el repositorio omite deliberadamente. Nada de esto
es configuración del gateway; es un componente delante de él. Es la ruta
crítica.

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 2.1 | diseñar el borde: proxy, terminación TLS, método de autenticación, autorización por endpoint si aplica | 2 | -- | diseño aprobado, origen del certificado identificado |
| 2.2 | levantar el proxy con certificado válido y renovación automática | 2 | 1.2, 2.1 | handshake limpio, renovación *comprobada*, no supuesta |
| 2.3 | implementar autenticación, emitir credenciales por aplicación llamante | 3 | 2.2 | una solicitud sin autenticar se rechaza en el borde |
| 2.4 | fijar `FORWARDED_ALLOW_IPS` al CIDR del proxy -- nunca `*` | 1 | 2.3 | dos clientes a través del proxy reciben cada uno su propia cuota |
| 2.5 | confirmar que el puerto del gateway solo es alcanzable por el borde, y el del motor por nadie | 1 | 2.4 | un escaneo desde afuera muestra solo el borde |
| 2.6 | secretos: nada sensible en `deploy/env/app.env`, que está versionado | 0.5 | -- | ninguna credencial en archivo versionado |
| 2.7 | revisión de exposición y aprobación | 2 | 2.5 | aprobación de seguridad registrada |
| 2.8 | replicar el borde completo en producción | 1 | 1.7 | borde de producción verificado independientemente contra staging |

### IT-3 Datos geoespaciales -- 6 días, semanas 2-3 y 7

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 3.1 | `make download-data` | 0.5 | 1.2 | extracto presente, suma de verificación registrada |
| 3.2 | `make process-osrm PROFILE=car`, o `make jail-data`; tiempo de máquina, propenso a OOM en host pequeño | 1 | 3.1 | grafo construido, memoria pico registrada |
| 3.3 | cada perfil adicional: una segunda pasada y un segundo proceso de motor | 1 c/u | 3.2 | un motor por perfil, cada uno con su propia URL |
| 3.4 | **validación de ajuste vial** -- direcciones reales de depósitos y clientes por `/nearest` | 2 | 3.2 | distribución de distancias de ajuste revisada con FN-2, atípicos explicados |
| 3.5 | acordar cadencia de refresco y quién la dispara | 0.5 | 3.4 | cadencia documentada con dueño nombrado |
| 3.6 | reconstruir para producción, verificado contra el grafo de staging | 1 | 1.7 | mismo extracto de origen, mismos perfiles |

Conviene decirle esto al equipo funcional temprano, del whitepaper 01: una línea
recta subestima un recorrido real en Costa Rica en ~40% en la mediana y en 17.6
veces en el peor par muestreado -- a 914 m de distancia, 16.1 km por carretera --
y dos tercios de los pares de direcciones del GAM difieren de ida contra vuelta.
Los planes se sentirán contraintuitivos al principio, y normalmente tendrán
razón.

### IT-4 Aplicación y liberación -- 8 días, semanas 3-4, 7, 9

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 4.1 | etiquetar la línea base de liberación desde un árbol en verde | 0.5 | -- | etiqueta existe, CI en verde sobre ella |
| 4.2 | revisar cada configuración contra `gateway/src/config.rs` | 1 | 4.1 | línea base de configuración guardada con la liberación |
| 4.3 | desplegar a staging: `make compose-up` / `make jail-up`, luego `*-health` | 0.5 | 2.2, 3.2 | `/ready` pasa, cubriendo gateway y motor |
| 4.4 | prueba de humo de `/health`, `/ready`, `/metrics`, `/docs`, más una llamada real por endpoint | 0.5 | 4.3 | cada endpoint responde con datos de mapa reales |
| 4.5 | aplicar topes de capacidad desde la medición de IT-5 | 1 | 5.4 | topes fijados por medición, no por valores por defecto |
| 4.6 | postura de versionado; `API_SUNSET` compromete una fecha | 0.5 | FN-3 | integradores en `/v1`, fecha fijada o deliberadamente vacía |
| 4.7 | escribir y ensayar liberación y rollback | 1 | 4.3 | rollback ensayado en staging y cronometrado |
| 4.8 | compilar y desplegar producción | 1 | G3 | producción respondiendo detrás del borde |
| 4.9 | ejecución del corte | 0.5 | G5 | tráfico en la nueva ruta, rollback aún disponible |
| 4.10 | convención: los banderines del motor cambian en **ambos** archivos de despliegue, mismo commit | 0.5 | -- | registrado en la lista de revisión del equipo |

### IT-5 SRE y observabilidad -- 12 días, semanas 4-6 y 9-10

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 5.1 | raspar `/metrics` hacia Prometheus | 1 | 4.3 | series llegando y retenidas |
| 5.2 | tableros: percentiles de latencia, tasa de error, tasa de aciertos de caché, comportamiento aguas arriba | 2 | 5.1 | renderiza bien; sin dependencia de `process_*` en el jail |
| 5.3 | alertas: tasa de 5xx, p95, `/ready` fallando, motor inalcanzable, Redis caído, rechazos por límite de tasa subiendo | 2 | 5.2 | cada una disparada deliberadamente una vez y observada |
| 5.4 | **capacidad**: `make loadtest` a la tasa esperada, luego `make capacity` escalonado con guarda de OOM, usando `--forwarded-for-pool` | 2 | 5.1 | techo de RSS medido y concurrencia segura entregados a IT-4 |
| 5.5 | envío y retención de bitácoras | 1 | 4.3 | bitácoras consultables, retención acordada |
| 5.6 | adaptar `docs/RUNBOOK.md` al runbook local de operación | 2 | 5.3 | otra persona de ingeniería reinicia la pila con él, sin ayuda |
| 5.7 | documentar el RTO: sin estado persistente, la recuperación es reconstruir y redesplegar | 1 | 3.2 | recuperación cronometrada una vez, de punta a punta |
| 5.8 | rol de guardia, ruta de escalamiento, dotación de hipercuidado | 1 | 5.6 | rol publicado antes del corte |

La indisponibilidad de Redis merece alerta propia: el limitador cae a conteo en
memoria por proceso en lugar de fallar solicitudes, así que el límite efectivo
se multiplica calladamente por la cantidad de workers. Queda disponible, y mal.

### IT-6 Calidad y validación -- 9 días, semanas 4-6 y 9

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 6.1 | `make test`, `make lint`, `cargo test` sobre el commit de liberación | 0.5 | 4.1 | en verde, resultado adjunto a la etiqueta |
| 6.2 | cablear la compuerta de repetición de paridad al CI; `make parity-selfcheck` valida el arnés sin conexión | 1 | 6.1 | compuerta de regresión bloqueando integraciones |
| 6.3 | `make examples-check` contra staging | 1 | 4.4 | todos los ejemplos pasan contra la instancia desplegada |
| 6.4 | aceptación de endpoints contra `docs/API_REFERENCE.md` | 3 | 4.4 | evidencia firmada por endpoint |
| 6.5 | pruebas negativas: matriz sobredimensionada, traza demasiado larga, paradas sobre el tope, saturación de cola | 2 | 6.4 | los rechazos son diagnosticables, no 500s |
| 6.6 | armar el paquete de aceptación para la compuerta | 1 | 6.5, 5.4 | evidencia completa, recomendación emitida |
| 6.7 | verificación de producción después del corte | 0.5 | 4.9 | el mismo paquete de humo en verde en producción |

Una corrida que reporta 100% de errores de transporte con latencias de ~2 ms en
todos los endpoints, `/health` y `/metrics` incluidos, apunta a la nada. Eso es
conexión rechazada, no estrés. `LOADTEST_URL` apunta por defecto al puerto del
jail; la ruta Docker necesita que se pase explícitamente.

### FN-1 Análisis de negocio -- 8 días, semana 1 y semana 5

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 7.1 | **brecha contra lo que hace `/vrp`**: asignación a depósitos y secuenciación por vehículo según capacidad y geografía, y nada sobre el tiempo | 3 | -- | cada requisito marcado como cumplido, rodeado o fuera de alcance -- firmado |
| 7.2 | dimensionamiento: paradas/día, paradas/resolución, vehículos, depósitos, perfiles, resoluciones concurrentes pico, ventana de planificación | 2 | 7.1 | números entregados a IT-1 |
| 7.3 | definir qué hace aceptable un plan, en el lenguaje del despacho | 2 | 7.1 | criterios que FN-2 pueda aplicar a diario |
| 7.4 | levantar la línea base de indicadores actuales antes de cambiar nada | 1 | -- | registrada; no hay segunda oportunidad para esto |

7.1 va antes que todo. Si la respuesta es que el despacho necesita ventanas de
entrega, el programa se detiene y se convierte en un proyecto de desarrollo.

### FN-2 Operación de flota y despacho -- 22 días, semanas 3-5 y 7-10

El mayor esfuerzo funcional.

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 8.1 | datos maestros de depósitos, cada uno contrastado con la vía por la que realmente salen los vehículos | 2 | 3.2 | cada depósito verificado en un mapa, no en una hoja de cálculo |
| 8.2 | capacidades de vehículo, en la misma unidad en que se expresa la demanda | 2 | 7.2 | lista de flota completa y consistente en unidades |
| 8.3 | **calidad de geocodificación de paradas** -- el mayor riesgo funcional | 5 | 3.4 | atípicos de ajuste corregidos o aceptados |
| 8.4 | aprender a leer un plan; correr `make examples` contra staging con datos reales | 3 | 4.4 | los despachadores interpretan un plan sin ayuda |
| 8.5 | aplicar los criterios de aceptación a planes producidos, registrando desacuerdos | 2 | 7.3, 8.4 | muestra revisada con veredictos registrados |
| 8.6 | **marcha en paralelo** -- planificar el día de ambas formas y comparar antes de comprometerse | 8 | G3 | dos semanas lado a lado, varianza explicada |
| 8.7 | manejo de excepciones: plan rechazado, parada que no ajusta, servicio no disponible | 2 | 8.6 | un plan B que no requiera a TI para ejecutarse |
| 8.8 | operación en vivo con revisión diaria durante el hipercuidado | 10 | G5 | indicadores siguiendo la línea base de 7.4 |

### FN-3 Integración y aplicaciones cliente -- 14 días, semanas 4-7 y 9

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 9.1 | acordar los endpoints que llamará el cliente, desde `API_REFERENCE.md` y el `/docs` en vivo | 1 | 4.4 | lista de endpoints acordada con FN-1 |
| 9.2 | construir contra **`/v1`**, no contra la raíz sin versión | 5 | 9.1, 2.3 | funciona de punta a punta a través del borde autenticado |
| 9.3 | manejar 422 (sobre un tope documentado), 429 (límite de tasa) y 503 (descarte de cola) de forma distinta | 2 | 9.2 | cada ruta ejercitada deliberadamente contra staging |
| 9.4 | respetar los topes del lado del cliente; fragmentar antes de enviar en vez de ser rechazado | 2 | 9.3 | ningún 422 evitable en un día normal |
| 9.5 | manejo y rotación de credenciales | 1 | 2.3 | rotación posible sin cambio de código |
| 9.6 | soporte al piloto y atención de defectos | 2 | 8.6 | defectos del piloto cerrados o aceptados |
| 9.7 | reapuntar a producción en el corte | 1 | 4.8 | cliente en producción, staging todavía disponible |

### FN-4 Capacitación y gestión del cambio -- 9 días, semanas 6-10

| # | Actividad | Días | Depende de | Criterio de salida |
|---|---|---|---|---|
| 10.1 | escribir el procedimiento del despachador a partir de planes que FN-2 produjo de verdad, no de la documentación de la API | 3 | 8.5 | revisado por un despachador que no lo escribió |
| 10.2 | capacitar despachadores, incluyendo por qué la distancia vial contradice el mapa que tienen en la cabeza | 2 | 10.1 | cada despachador ha planificado un día en el sistema |
| 10.3 | informar a conductores y supervisores sobre qué les cambia | 1 | 10.1 | entregado antes del primer día en vivo |
| 10.4 | acompañamiento en piso durante las primeras semanas en vivo | 2 | G5 | preguntas respondidas en el piso, no por ticket |
| 10.5 | canalizar brechas reales a FN-1 como solicitudes de cambio | 1 | 10.4 | backlog con dueño y priorizado |

## Listas de verificación

Ordenadas para el día en que se hace el trabajo. Cada línea es un resultado
verificable.

**IT-1 Plataforma.** Ruta de despliegue por escrito. Host dimensionado contra
cantidad de perfiles y resoluciones concurrentes pico. Staging aprovisionado y
alcanzable. Disco dimensionado para extracto más grafo, por perfil. Puerto del
motor inalcanzable desde fuera del host. *Jail:* `make jail-host` ejecutado;
`jail-doctor` confirma `delayed_ack=0` aplicado y persistido. Línea base de
doctor registrada. Postura de HA decidida y ventana de caída aceptada por
escrito. Producción replica staging, salidas de doctor comparadas.

**IT-2 Seguridad.** Diseño del borde aprobado. Certificado instalado, renovación
comprobada. Autenticación exigida -- una llamada sin autenticar se rechaza.
Credenciales emitidas por aplicación llamante. `FORWARDED_ALLOW_IPS` fijado al
CIDR del proxy. Confirmado que **no** es `*`. Cubetas por cliente comprobadas con
dos clientes. Gateway alcanzable solo por el borde, con evidencia de escaneo.
Ninguna credencial en archivo versionado. Revisión de exposición aprobada.

**IT-3 Geodatos.** Extracto descargado, suma de verificación registrada. Grafo
construido para cada perfil requerido. Memoria pico de construcción registrada.
Un motor por perfil. Direcciones reales pasadas por `/nearest`. Atípicos de
ajuste revisados con despacho. Cadencia de refresco acordada con dueño nombrado.
Grafo o imagen archivados para que la recuperación no tenga que reconstruir.

**IT-4 Liberación.** Commit de liberación etiquetado desde árbol en verde. Cada
configuración revisada contra `config.rs`. Desplegado y `*-health` en verde. Una
llamada real contra cada endpoint. Topes de capacidad fijados desde la medición
de IT-5. Integradores en `/v1`, `API_SUNSET` decidido. Rollback ensayado y
cronometrado. Convención de ambos archivos registrada para los banderines del
motor.

**IT-5 SRE.** `/metrics` raspado y retenido. Tableros independientes de
`process_*` en FreeBSD. Alertas de 5xx, p95, `/ready`, motor inalcanzable e
indisponibilidad de Redis. Cada alerta disparada una vez deliberadamente.
Prueba de carga con pool de forwarded-for. Evaluación de capacidad completa,
techo de RSS entregado a IT-4. Bitácoras enviadas con retención acordada.
Runbook local comprobado por otra persona de ingeniería. Recuperación
cronometrada, RTO publicado. Rol de guardia activo antes del corte.

**IT-6 Calidad.** Suite, lint y pruebas de cargo en verde sobre el commit de
liberación. Compuerta de repetición de paridad bloqueando en CI.
`make examples-check` pasa contra staging. Cada endpoint aceptado contra la
referencia de API. Matriz sobredimensionada rechazada limpiamente. Traza
demasiado larga rechazada con 422, no con 500. Paradas sobre el tope rechazadas;
saturación de cola descarta con 503. Resultado de prueba de carga contrastado
por sensatez. Paquete de aceptación armado.

**FN-1 Análisis.** Brecha firmada *antes de aprovisionar nada*. Confirmado si se
requieren ventanas de tiempo. Confirmado si se requieren tiempos de servicio,
jornadas o competencias. Aceptado que los planes asumen velocidad de flujo
libre. Dimensionamiento entregado a IT-1. Criterios de aceptación escritos en el
lenguaje del despacho. Línea base de indicadores levantada antes de cambiar nada.

**FN-2 Despacho.** Cada depósito verificado en un mapa. Capacidades en la misma
unidad que la demanda. Geocodificación de paradas revisada, ajustes lejanos
corregidos o aceptados. Los despachadores leen una asignación y una secuencia
sin ayuda. Criterios aplicados a una muestra real, veredictos registrados. Dos
semanas de marcha en paralelo completas. Cada varianza material explicada, no
solo anotada. Plan B escrito que no requiera a TI. Revisión diaria de
indicadores en marcha.

**FN-3 Integración.** Lista de endpoints acordada. El cliente llama `/v1`, no la
raíz deprecada. Autenticado a través del borde. 422 tratado como corregir la
solicitud, no como reintentar. 429 con retroceso exponencial. 503 por descarte
de cola distinguido de una caída. El cliente fragmenta por debajo de los topes.
Rotación de credenciales sin cambio de código.

**FN-4 Capacitación.** Procedimiento escrito desde planes reales. Procedimiento
revisado por un despachador que no lo escribió. Cada despachador ha planificado
un día completo. Intuición de distancia vial cubierta. Conductores y supervisores
informados. Acompañamiento en piso dotado. Retroalimentación canalizada a FN-1.

## Compuertas

| Compuerta | Semana | Pregunta | Evidencia | Preside |
|---|---|---|---|---|
| G0 | 1 | ¿puede el optimizador desplegado expresar lo que el despacho necesita? | brecha firmada, dimensionamiento, ruta escogida | FN-1 |
| G1 | 3 | ¿está staging de pie y respondiendo con datos de mapa reales? | `*-health` en verde, una llamada real por endpoint, línea base de configuración | IT-4 |
| G2 | 5 | ¿el borde es real y el contrato se cumple? | llamada sin autenticar rechazada, cubetas por cliente comprobadas, aceptación firmada | IT-2 + IT-6 |
| G3 | 6 | ¿aguanta bajo carga, y podemos verlo? | corrida de capacidad, topes desde medición, cada alerta disparada | IT-5 |
| G4 | 8 | ¿los planes son manejables? | dos semanas en paralelo, varianza explicada, aprobación de despacho | FN-2 |
| G5 | 9 | ¿salimos en vivo? | todo lo anterior, rollback ensayado, guardia activa, capacitación entregada | Programa |

## Después de la puesta en marcha

| Cadencia | Actividad | Dueño |
|---|---|---|
| diaria | el despacho revisa planes contra los criterios de aceptación, excepciones registradas | FN-2 |
| semanal | revisión de indicadores contra la línea base previa al programa | FN-1, FN-2 |
| mensual | refresco del mapa y redespliegue, luego repetir la muestra de ajuste vial | IT-3 |
| mensual | revisión de capacidad contra el tráfico medido | IT-5 |
| trimestral | parcheo de dependencias e imágenes base, recompilar, repetir aceptación | IT-4 |
| trimestral | simulacro de rotación de certificados y credenciales | IT-2 |
| al cambiar | banderines del motor y límites de recursos en **ambos** archivos de despliegue, mismo commit | IT-4 |
| una vez | retirar las rutas raíz sin versión; fijar `API_SUNSET` para comprometer la fecha | IT-4 |

## Supuestos, y qué cambiaría esto

Se supone: un ambiente de producción más staging, una región, una ruta de
despliegue; un perfil de ruteo al salir en vivo; una aplicación de despacho o de
pedidos existente con la cual integrar, así que no se construye interfaz de
usuario nueva; equipos a tiempo parcial, razón por la cual las semanas
transcurridas superan la suma de días hábiles; que TI puede proveer un proxy
inverso; operación de nodo único aceptable con un tiempo de recuperación
documentado.

Qué cambiaría el plan de forma material:

- **Se requieren ventanas de entrega.** El programa se vuelve un proyecto de
  desarrollo. Redefina el alcance en G0 en vez de rodearlo con procedimiento de
  despacho.
- **Se requiere alta disponibilidad.** Dos nodos, un balanceador, Redis
  compartido para los contadores de límite de tasa, y las pruebas para comprobar
  la conmutación. Nada en el repositorio hace esto hoy; presupueste tres a
  cuatro semanas.
- **Más de una región o extracto.** Multiplica el frente de datos y la huella de
  servidores. El gateway en sí no se ve afectado.
- **Matrices más anchas que 100 coordenadas.** Un banderín del motor en ambas
  definiciones de despliegue, y volver a medir memoria después.
- **El jail es el destino y tiene poca memoria.** Los tiempos de construcción se
  alargan y el extracto puede toparse con el asesino por falta de memoria.
  Agregue una semana a los frentes de datos y construcción.
