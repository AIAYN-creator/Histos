# Histos

Herramienta open source y agnóstica de agente que usa **Obsidian Canvas** como tablero compartido humano-IA para gestionar proyectos de producción escrita: TFGs, tesis, papers, posts de blog, informes.

El agente propone el flujo de trabajo y redacta las tareas; el humano aprueba los cambios reales antes de que se apliquen al contenido.

> Estado: esquema formal ([`src/histos/schema/`](src/histos/schema/)) y CLI mínimo ([`src/histos/`](src/histos/), 12 comandos, `pytest` en verde) implementados. En dogfooding activo sobre un TFG real. El plugin de Obsidian todavía no existe.

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

En un vault recién inicializado (sin tarjetas), Loop 1 empieza con una entrevista breve — de qué trata el proyecto, tipo de trabajo, si hay una estructura obligatoria, si conviene modelar checkpoints de revisión como tarjetas — antes de proponer el índice inicial. Protocolo completo en [`AGENTS.md`](src/histos/templates/AGENTS.md).

### Loop 2 — Ejecución (el agente trabaja, el humano decide)

1. El usuario asigna una o varias tarjetas al agente (por id)
2. El agente recibe como contexto: el `.md` de esa tarjeta, el contenido de las tarjetas upstream (dependencias) y el brief general del proyecto
3. El agente redacta una propuesta de contenido
4. El agente **nunca** escribe directamente en el `.md` canónico — solo puede proponer
5. La tarjeta pasa a estado "propuesta pendiente de revisión"
6. El usuario revisa cuando puede (no hace falta estar presente mientras el agente trabaja) y ve un diff antes/después
7. El usuario aprueba (se aplica el cambio real, tarjeta → "aprobada") o rechaza (no se toca el `.md`, tarjeta vuelve a backlog o queda marcada para reintento con feedback)

**Propiedad clave — seguridad en modo AFK:** como el agente solo puede proponer y nunca escribir directo, es seguro dejarlo procesando una cola de tarjetas sin supervisión *mientras el agente respete las reglas de `AGENTS.md`*. El peor caso posible, con un agente que las sigue, es encontrar varias tarjetas en amarillo esperando revisión al volver — nunca contenido real escrito sin autorización. Ojo: esto es una convención que el agente cumple, no una barrera técnica que se lo impida — ver [Modelo de confianza](#modelo-de-confianza).

### Grafo de dependencias

Representado mediante los edges del canvas como un DAG (grafo acíclico dirigido). El agente respeta el orden topológico al elegir qué tarjeta trabajar a continuación. Si una acción rompería o ignoraría una dependencia existente, el agente debe avisar y pedir autorización **antes** de proceder, nunca después.

## Modelo de confianza

La regla "el agente nunca escribe directamente en `content/*.md`" es una **convención de prosa, no una garantía técnica**. Vive en [`AGENTS.md`](src/histos/templates/AGENTS.md), se carga en el contexto del agente, y depende de que el agente decida cumplirla. No hay permisos de sistema de ficheros, proceso intermedio, ni git hook que la haga cumplir — `content/` es una carpeta de escritura normal. Cualquier proceso con acceso de escritura al vault, incluido el propio agente usando sus herramientas de edición de fichero en vez de `histos propose`, puede saltársela sin que nada lo impida a nivel de herramienta.

Es una decisión consciente, no un descuido: el caso de uso actual es un único usuario con un agente de confianza que lee y sigue instrucciones — el mismo nivel de confianza que cualquier `CLAUDE.md`/`AGENTS.md` de cualquier repo. Deja de ser suficiente si el agente no es de confianza (instrucciones inyectadas, modelo no alineado) o si varios usuarios/agentes con intereses distintos comparten el mismo vault.

Una garantía real requeriría separar "quién tiene permiso de escritura en `content/`" de "el agente" a nivel de sistema operativo — por ejemplo, que solo el propio binario `histos` (no el agente directamente) tuviera permisos de escritura ahí, forzando que cualquier cambio pase por él. En un escritorio de un solo usuario esto no es trivial (agente y CLI corren como el mismo usuario del SO) — haría falta algo como una cuenta de sistema separada o un proceso intermediario con permisos elevados. Sandboxing real del agente es trabajo futuro explícito, no implementado todavía.

## Convenciones del canvas

> Esquema formal y machine-checkable: [`src/histos/schema/histos-canvas.schema.json`](src/histos/schema/histos-canvas.schema.json) — detalle completo en [`docs/canvas-schema.md`](docs/canvas-schema.md).

- **Node type:** `file` — cada tarjeta apunta a un `.md` real del vault, no contiene texto embebido. (`text` se usa únicamente para la leyenda de colores decorativa que genera `histos init`; el CLI la ignora por completo.)
- **Layout:** lectura tipo diagrama de Gantt pero sin fechas de calendario — la columna (x) es el rango de dependencia (camino más largo desde una raíz), las tarjetas del mismo rango se apilan en vertical. El tamaño de cada tarjeta se calcula a partir de la longitud de su `description`. `add-card`, `link` y `describe` recalculan tamaño+posición de todas las tarjetas automáticamente. No es un dagre completo (sin minimización de cruces de edges), pero cubre el caso de uso real.

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

Cada comando opera sobre el directorio actual, que debe ser la raíz de un vault Histos (`histos init` la crea). Ningún comando bloquea en un prompt interactivo, así que una cola de tarjetas se puede procesar en modo AFK con un simple bucle en el agente que invoca el CLI — no hace falta ningún flag ni modo especial.

```bash
histos init                                            # crea project.canvas (con leyenda de colores) + content/
histos add-card cap1 --title "Intro" [--description "..."]
                                                          # tarjeta suelta -> Backlog
histos add-card cap2 --title "Cap 2" --depends-on cap1 --authorized
                                                          # --authorized es obligatorio en cuanto se toca el grafo
                                                          # de dependencias (Loop 1) -- sin prompt: una casilla que
                                                          # el agente marca solo tras pedir permiso en la conversación
histos link cap1 --depends-on cap0 --authorized         # añade dependencia a una tarjeta YA existente
histos describe cap1 --text "..." [--sources f1 f2]     # descripción y/o fuentes externas (frontmatter, sin permiso)
histos assign cap1 [--by agent|human]                   # -> En progreso
histos context cap1                                     # junta descripción+dependencias aprobadas+sources+PROJECT.md
histos propose cap1 --file borrador.md                  # -> Propuesta pendiente de revisión
histos diff cap1                                        # diff entre content/cap1.md y la propuesta pendiente
histos approve cap1                                     # aplica la propuesta al .md real -> Aprobada
histos reject cap1 [--feedback "..."]                   # descarta la propuesta -> Backlog, feedback en status_note
histos status                                           # tarjetas agrupadas por estado (recalcula Bloqueada/Backlog)
histos validate                                         # valida project.canvas contra el schema formal
```

### Abrir el vault en Obsidian

`histos init` crea `project.canvas` en el directorio actual — esa carpeta, **exactamente esa y ninguna por encima**, es la que tienes que abrir como vault en Obsidian (`Open folder as vault`). Canvas es una función nativa de Obsidian; no hace falta ningún plugin.

Por qué importa tanto: las tarjetas referencian sus `.md` con rutas relativas al vault (`content/cap1.md`). Si abres una carpeta por encima de la que contiene `project.canvas` (p. ej. el directorio padre en vez del propio vault), esas rutas ya no resuelven y Obsidian te muestra las tarjetas como "Create new note" / "Swap file..." en vez de con contenido — no está roto, es la carpeta equivocada. Si te pasa después de tener el vault bien abierto (p. ej. porque `histos` creó ficheros mientras Obsidian ya estaba abierto), recarga con `Ctrl+R` antes de sospechar de nada más.

Código en [`src/histos/`](src/histos/), tests en [`tests/`](tests/) (`pytest`). Guía práctica de uso día a día (para humanos, no para agentes): [`docs/usage.md`](docs/usage.md).

## Alcance del MVP (v1)

**Dentro:**
- Esquema formal del `.canvas` con la convención de colores y node types de arriba
- Uso de frontmatter en los `.md` para metadatos que Obsidian no interpreta nativamente
- CLI mínimo (Python, agnóstico de agente): `init`, `add-card`, `link`, `describe`, `assign`, `context`, `propose`, `diff`, `approve`, `reject`, `status`, `validate` — implementado y probado ([`src/histos/`](src/histos/), [`tests/`](tests/))
- Modo no interactivo del CLI para trabajo AFK — resuelto: ningún comando usa prompts interactivos, no hace falta flag especial
- Instrucciones para agentes documentando esquema, leyenda de colores y reglas de autorización de los dos loops — implementado como [`AGENTS.md`](src/histos/templates/AGENTS.md) (fuente única, agnóstico de agente) + `CLAUDE.md` (una línea, `@AGENTS.md`), que `histos init` copia a cada vault nuevo
- Flujo de aprobación de escritura real vía diff antes de tocar un `.md` canónico
- Dogfooding sobre un proyecto real de TFG

**Fuera de v1:**
- Plugin nativo de Obsidian (TypeScript, Obsidian Plugin API) — posible v2 para interactividad en vivo
- Integración de git como backend de historial del contenido del usuario
- Fechas / calendario real tipo Gantt clásico
- Traer contexto externo (p. ej. el `.tex` principal en Overleaf vía su integración Git, que requiere plan de pago) para que `histos context` (ver preguntas abiertas) lo incluya automáticamente — posible v2
- Sandboxing real del agente (ver [Modelo de confianza](#modelo-de-confianza)): que `propose`/`approve` sea una barrera técnica y no solo una convención de `AGENTS.md` — próximo en la agenda
- Soporte multi-agente: varios agentes trabajando en paralelo sobre el mismo vault — depende del sandboxing anterior para no pisarse entre sí

## Alcance de la distribución

Lo que se publica en este repo es la **herramienta** (CLI, esquema, prompt, documentación) como proyecto open source. Git/GitHub no es el backend de versionado del contenido de los proyectos de cada usuario — eso queda fuera del scope de v1 y es decisión de cada usuario.

## Prior art

- **Kanvas (XMihura)** — referencia directa de arquitectura. Mismo patrón pero orientado a código: prompt + CLI en Python que el agente usa para tocar el canvas + el `.canvas` en sí. Sin SaaS, sin build step, agnóstico de agente. Histos adapta ese patrón a proyectos de escritura en vez de código.
- **claude-canvas (AgriciDaniel)** — referencia solo para el algoritmo de auto-layout (`dagre`) y la idea de zonas/grupos visuales. No es la arquitectura base.
- **JSON Canvas spec (jsoncanvas.org)** — el formato `.canvas` es el estándar abierto JSON Canvas, no propietario de Obsidian. Node types disponibles: `text` (markdown embebido), `file` (ruta a un fichero real del vault), `link` (URL), `group` (contenedor puramente geométrico — un nodo "pertenece" a un grupo solo si sus coordenadas caen dentro del rectángulo del grupo; no hay `parent_id` en los datos).

## Preguntas abiertas

- Formato exacto del brief general del proyecto — **resuelto en parte:** `histos context <id>` ya junta descripción + contenido aprobado de las dependencias + `sources` externas (`.txt`/`.md`/`.tex`/`.docx`, registradas con `describe --sources`) + `PROJECT.md` si existe. Lo que sigue abierto: `PROJECT.md` en sí no tiene formato definido todavía, simplemente se incluye tal cual si está presente
- Estructura de carpetas — **resuelto en parte:** las tarjetas viven en `content/<slug>.md` (ver [`docs/canvas-schema.md`](docs/canvas-schema.md)); subcarpetas adicionales como `content/borradores/` siguen abiertas
