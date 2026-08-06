# Trellis

Herramienta open source y agnóstica de agente que usa **Obsidian Canvas** como tablero compartido humano-IA para gestionar proyectos de producción escrita: TFGs, tesis, papers, posts de blog, informes.

El agente propone el flujo de trabajo y redacta las tareas; el humano aprueba los cambios reales antes de que se apliquen al contenido.

> Estado: esquema formal ([`src/trellis/schema/`](src/trellis/schema/)) y CLI mínimo ([`src/trellis/`](src/trellis/), 9 comandos, `pytest` en verde) implementados y probados a mano en un vault real fuera de este repo. Siguiente paso: dogfooding sobre un TFG real. El `CLAUDE.md`/system prompt para agentes y el plugin de Obsidian todavía no existen.

## Idea central

Separación estricta entre el **mapa del proyecto** (`canvas.json`) y el **contenido real** (ficheros `.md`). El canvas nunca contiene prosa — solo referencias a ficheros, metadatos de estado y el grafo de dependencias.

Todo vive en local, en un vault de Obsidian normal (una carpeta con subcarpeta `.obsidian/`). No hay servidor propio ni backend remoto; el único salto fuera de local es la llamada al agente/LLM.

## Los dos loops

### Loop 1 — Planificación (el agente tiene libertad casi total)

El agente puede libremente:
- Crear, editar y mover tarjetas
- Cambiar el estado/color de una tarjeta
- Editar el texto/descripción de una tarjeta
- Agrupar tarjetas visualmente (grupos geométricos; sin jerarquía rígida en los datos)

Requiere autorización explícita del usuario:
- Crear, borrar o redirigir una **arista de dependencia** — porque redefine qué se desbloquea y en qué orden, no es un cambio puramente estético

El agente decide si agrupar tarjetas según la envergadura del proyecto (un post de blog probablemente no necesita grupos; una tesis probablemente sí). No se fuerza jerarquía en v1.

### Loop 2 — Ejecución (el agente trabaja, el humano decide)

1. El usuario asigna una o varias tarjetas al agente (por id)
2. El agente recibe como contexto: el `.md` de esa tarjeta, el contenido de las tarjetas upstream (dependencias) y el brief general del proyecto
3. El agente redacta una propuesta de contenido
4. El agente **nunca** escribe directamente en el `.md` canónico — solo puede proponer
5. La tarjeta pasa a estado "propuesta pendiente de revisión"
6. El usuario revisa cuando puede (no hace falta estar presente mientras el agente trabaja) y ve un diff antes/después
7. El usuario aprueba (se aplica el cambio real, tarjeta → "aprobada") o rechaza (no se toca el `.md`, tarjeta vuelve a backlog o queda marcada para reintento con feedback)

**Propiedad clave — seguridad en modo AFK:** como el agente solo puede proponer y nunca escribir directo, es seguro dejarlo procesando una cola de tarjetas sin supervisión. El peor caso posible es encontrar varias tarjetas en amarillo esperando revisión al volver — nunca contenido real escrito sin autorización. Es una propiedad estructural, no un comportamiento a vigilar.

### Grafo de dependencias

Representado mediante los edges del canvas como un DAG (grafo acíclico dirigido). El agente respeta el orden topológico al elegir qué tarjeta trabajar a continuación. Si una acción rompería o ignoraría una dependencia existente, el agente debe avisar y pedir autorización **antes** de proceder, nunca después.

## Convenciones del canvas

> Esquema formal y machine-checkable: [`src/trellis/schema/trellis-canvas.schema.json`](src/trellis/schema/trellis-canvas.schema.json) — detalle completo en [`docs/canvas-schema.md`](docs/canvas-schema.md).

- **Node type:** `file` — cada tarjeta apunta a un `.md` real del vault, no contiene texto embebido.
- **Layout:** lectura tipo diagrama de Gantt pero sin fechas de calendario — la posición horizontal aproxima el orden topológico de dependencias. Auto-layout tipo `dagre` al reestructurar.

### Leyenda de colores

| Estado | Color | Preset | Significado |
|---|---|---|---|
| Backlog | morado | `"6"` | Tarea pendiente, aún no empezada |
| En progreso | naranja | `"2"` | El agente o el usuario está trabajando en ella activamente |
| Bloqueada | rojo | `"1"` | No se puede empezar porque depende de una tarjeta anterior sin cerrar (estado derivado, lo calcula el CLI) |
| Propuesta pendiente de revisión | amarillo | `"3"` | El agente propuso contenido y espera aprobación (loop 2) |
| Solicitud cambio de dependencia | cian | `"5"` | El agente quiere modificar el grafo de dependencias y espera autorización (loop 1) |
| Aprobada | verde | `"4"` | Cambio aceptado por el usuario y aplicado al contenido real |

## Metadatos

Lo que Obsidian/JSON Canvas no interpreta de forma nativa (duración estimada, duración real, a quién está asignada la tarea, notas de por qué está bloqueada) no se fuerza dentro del `.canvas` — se guarda como **frontmatter YAML** al principio de cada `.md`, formato que Obsidian ya soporta nativamente. Así el `.canvas` se mantiene 100% compatible con Obsidian estándar.

Campos: `estimated_duration_hours`, `actual_duration_hours`, `assigned_to`, `status_note`. El histórico estimado vs. real permitirá calibrar con el tiempo qué tan fiables son las estimaciones del agente para este tipo de tareas.

## CLI

Comandos concretos y con nombre claro en vez de que el agente edite el JSON del canvas a mano, para evitar romper el formato y limitar al agente a un conjunto conocido de operaciones seguras (igual que una API). Agnóstico de agente: funciona con cualquier herramienta que ejecute comandos de shell y lea/escriba ficheros (Claude Code, Codex, Gemini CLI...).

Instalación (editable, para desarrollo):

```bash
pip install -e .
```

Cada comando opera sobre el directorio actual, que debe ser la raíz de un vault Trellis (`trellis init` la crea). Ningún comando bloquea en un prompt interactivo, así que una cola de tarjetas se puede procesar en modo AFK con un simple bucle en el agente que invoca el CLI — no hace falta ningún flag ni modo especial.

```bash
trellis init                                            # crea project.canvas + content/ + .trellis/proposals/
trellis add-card cap1 --title "Intro"                    # tarjeta suelta -> Backlog
trellis add-card cap2 --title "Cap 2" --depends-on cap1 --authorized
                                                          # --authorized es obligatorio en cuanto se toca el grafo
                                                          # de dependencias (Loop 1) -- sin prompt: una casilla que
                                                          # el agente marca solo tras pedir permiso en la conversación
trellis assign cap1 [--by agent|human]                   # -> En progreso
trellis propose cap1 --file borrador.md                  # -> Propuesta pendiente de revisión
trellis diff cap1                                        # diff entre content/cap1.md y la propuesta pendiente
trellis approve cap1                                     # aplica la propuesta al .md real -> Aprobada
trellis reject cap1 [--feedback "..."]                   # descarta la propuesta -> Backlog, feedback en status_note
trellis status                                           # tarjetas agrupadas por estado (recalcula Bloqueada/Backlog)
trellis validate                                         # valida project.canvas contra el schema formal
```

Código en [`src/trellis/`](src/trellis/), tests en [`tests/`](tests/) (`pytest`). Guía práctica de uso día a día (para humanos, no para agentes): [`docs/usage.md`](docs/usage.md).

## Alcance del MVP (v1)

**Dentro:**
- Esquema formal del `.canvas` con la convención de colores y node types de arriba
- Uso de frontmatter en los `.md` para metadatos que Obsidian no interpreta nativamente
- CLI mínimo (Python, agnóstico de agente): `init`, `add-card`, `assign`, `propose`, `diff`, `approve`, `reject`, `status`, `validate` — implementado y probado ([`src/trellis/`](src/trellis/), [`tests/`](tests/))
- Modo no interactivo del CLI para trabajo AFK — resuelto: ningún comando usa prompts interactivos, no hace falta flag especial
- Instrucciones para agentes documentando esquema, leyenda de colores y reglas de autorización de los dos loops — implementado como [`AGENTS.md`](src/trellis/templates/AGENTS.md) (fuente única, agnóstico de agente) + `CLAUDE.md` (una línea, `@AGENTS.md`), que `trellis init` copia a cada vault nuevo
- Flujo de aprobación de escritura real vía diff antes de tocar un `.md` canónico
- Dogfooding sobre un proyecto real de TFG

**Fuera de v1:**
- Plugin nativo de Obsidian (TypeScript, Obsidian Plugin API) — posible v2 para interactividad en vivo
- Integración de git como backend de historial del contenido del usuario
- Fechas / calendario real tipo Gantt clásico

## Alcance de la distribución

Lo que se publica en este repo es la **herramienta** (CLI, esquema, prompt, documentación) como proyecto open source. Git/GitHub no es el backend de versionado del contenido de los proyectos de cada usuario — eso queda fuera del scope de v1 y es decisión de cada usuario.

## Prior art

- **Kanvas (XMihura)** — referencia directa de arquitectura. Mismo patrón pero orientado a código: prompt + CLI en Python que el agente usa para tocar el canvas + el `.canvas` en sí. Sin SaaS, sin build step, agnóstico de agente. Trellis adapta ese patrón a proyectos de escritura en vez de código.
- **claude-canvas (AgriciDaniel)** — referencia solo para el algoritmo de auto-layout (`dagre`) y la idea de zonas/grupos visuales. No es la arquitectura base.
- **JSON Canvas spec (jsoncanvas.org)** — el formato `.canvas` es el estándar abierto JSON Canvas, no propietario de Obsidian. Node types disponibles: `text` (markdown embebido), `file` (ruta a un fichero real del vault), `link` (URL), `group` (contenedor puramente geométrico — un nodo "pertenece" a un grupo solo si sus coordenadas caen dentro del rectángulo del grupo; no hay `parent_id` en los datos).

## Preguntas abiertas

- Formato exacto del brief general del proyecto pasado como contexto en loop 2 — propuesta de partida: un `PROJECT.md` fijo en la raíz del vault, a validar
- Estructura de carpetas — **resuelto en parte:** las tarjetas viven en `content/<slug>.md` (ver [`docs/canvas-schema.md`](docs/canvas-schema.md)); subcarpetas adicionales como `content/borradores/` siguen abiertas
