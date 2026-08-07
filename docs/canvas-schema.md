# Esquema del `.canvas`

Este documento formaliza las convenciones de Histos sobre el formato [JSON Canvas 1.0](https://jsoncanvas.org/spec/1.0/). El validador machine-checkable vive en [`src/histos/schema/histos-canvas.schema.json`](../src/histos/schema/histos-canvas.schema.json) (empaquetado junto al CLI); aquí está el porqué.

Lo que el JSON Schema **no** puede comprobar por sí solo — que `fromNode`/`toNode` de cada edge apunten a un `id` que existe, y que el grafo de dependencias sea acíclico — es responsabilidad del CLI (`histos status` / `histos validate`), no de este esquema.

## Estructura de carpetas

```
<vault>/
├── project.canvas
├── content/
│   └── <slug>.md        # una tarjeta = un fichero (version canonica actual)
├── propuestas/
│   └── <slug>.md        # borrador pendiente de aprobar/rechazar
└── aprobados/
    └── <slug>.md        # copia del borrador tal cual se aprobo (historico; rechazados NO se guardan)
```

Subcarpetas adicionales dentro de `content/` (p. ej. `borradores/`) quedan abiertas — no forman parte de esta convención todavía.

## Tipos de nodo

Histos usa tres de los cuatro node types de JSON Canvas:

- **`file`** — una tarjeta de tarea. Apunta a un `.md` real en `content/`; nunca contiene texto embebido. `link` suelto no está permitido.
- **`group`** — agrupación puramente geométrica (p. ej. "capítulo 3"). Un nodo "pertenece" a un grupo solo si sus coordenadas caen dentro del rectángulo del grupo — no hay `parent_id` en los datos. No lleva estado.
- **`text`** — únicamente para contenido decorativo/documentación (la leyenda de colores que `histos init` coloca en cada canvas nuevo). No es una tarjeta: el CLI lo ignora por completo — toda la lógica de estado/dependencias filtra explícitamente por `type == "file"`. Nada impide añadir más notas de texto a mano en Obsidian; Histos simplemente no las toca.

## Convención de id

El `id` de una tarjeta es el mismo slug que el nombre de su fichero: id `cap3` → `content/cap3.md`. Así los ejemplos del CLI (`histos assign cap3`, `--depends-on cap2`) son legibles directamente. Los `group` solo necesitan ser únicos, sin convención adicional.

## Color → estado

El color de una tarjeta (campo `color`, preset `"1"`–`"6"`) **es** su estado — no se duplica en ningún otro sitio:

| Preset | Color | Estado |
|---|---|---|
| `"1"` | rojo | Bloqueada |
| `"2"` | naranja | En progreso |
| `"3"` | amarillo | Propuesta pendiente de revisión |
| `"4"` | verde | Aprobada |
| `"5"` | cian | Solicitud cambio de dependencia |
| `"6"` | morado | Backlog |

Una tarjeta `file` sin `color` no está gestionada por Histos (añadida a mano en Obsidian, o dato corrupto) — el schema la rechaza porque `color` es obligatorio en `cardNode`, y eso es intencional: la propia validación del schema es el mecanismo de detección.

**Bloqueada es un estado derivado**, no algo que el agente o el humano asignen a mano: el CLI lo calcula viendo si *todas* las edges entrantes de una tarjeta apuntan a nodos ya en Aprobada (color `"4"`). Nada te impide poner color `"1"` manualmente, pero el CLI debería tratarlo como una señal a recalcular, no como fuente de verdad.

## Edges — semántica de dependencia

`fromNode → toNode` significa **"toNode depende de fromNode"**: fromNode debe llegar a Aprobada antes de que toNode pueda salir de Bloqueada. Coincide con el layout izquierda→derecha tipo Gantt del canvas y con el default del spec (`toEnd` = `"arrow"` apunta hacia toNode, es decir, hacia lo que se desbloquea).

## Frontmatter del `.md`

Lo que Obsidian/JSON Canvas no interpreta nativamente vive como YAML frontmatter en cada tarjeta — nunca en el `.canvas`:

| Campo | Tipo | Notas |
|---|---|---|
| `description` | string | una línea, resume la tarjeta; se pone en `add-card --description` o se actualiza después con `histos describe` — nunca toca el cuerpo, así que no pasa por `propose`/`approve` |
| `sources` | lista de strings | rutas a ficheros externos (`.txt`, `.md`, `.tex`, `.docx`) con material de referencia para esta tarjeta — `describe --sources` la sustituye entera, no la añade. `histos context <id>` los lee e incluye su texto |
| `estimated_duration_hours` | number | lo rellena el agente al aceptar/empezar la tarea |
| `actual_duration_hours` | number | se rellena al completarse, para comparar con la estimación |
| `assigned_to` | `"agent"` \| `"human"` | |
| `status_note` | string | texto libre, p. ej. motivo de bloqueo |

`status` deliberadamente no está aquí: vive como `color` en el canvas para no tener dos fuentes de verdad del mismo dato.

## Ejemplo mínimo

```jsonc
{
  "nodes": [
    { "id": "cap1", "type": "file", "x": 0,    "y": 0, "width": 250, "height": 100,
      "file": "content/cap1.md", "color": "4" },        // Aprobada
    { "id": "cap2", "type": "file", "x": 320,  "y": 0, "width": 250, "height": 100,
      "file": "content/cap2.md", "color": "3" },        // Propuesta pendiente de revision
    { "id": "cap3", "type": "file", "x": 640,  "y": 0, "width": 250, "height": 100,
      "file": "content/cap3.md", "color": "1" }         // Bloqueada (depende de cap2)
  ],
  "edges": [
    { "id": "e1", "fromNode": "cap1", "toNode": "cap2" },
    { "id": "e2", "fromNode": "cap2", "toNode": "cap3" }
  ]
}
```

Ver [`examples/example.canvas`](../examples/example.canvas) para uno completo, con un `group`.
