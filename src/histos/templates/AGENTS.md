# Instrucciones para agentes en este vault

Este directorio es un vault gestionado por **Histos**: un tablero de tareas en `project.canvas` (Obsidian Canvas) donde el color de cada tarjeta es su estado, y el contenido real vive en `content/*.md`. El CLI `histos` es la unica forma soportada de tocar el canvas -- nunca edites `project.canvas` a mano.

## Reglas duras (no negociables)

1. **Nunca escribas directamente en `content/*.md`, ni en ningun fichero registrado como `sources` de una tarjeta.** Ambos son de solo lectura para ti. El unico camino para que un cambio de contenido llegue al `.md` canonico es `histos propose <id> --file <borrador>` seguido de `histos approve <id>` por parte de un humano. Si necesitas redactar contenido, escribelo en un fichero aparte (el borrador) y pasalo a `propose` -- nunca edites `content/<id>.md` directamente. `propose` copia el borrador a `propuestas/<id>.md` (carpeta visible en Obsidian, no oculta) hasta que se apruebe o rechace; el humano puede leerla o incluso retocarla ahi antes de decidir -- eso es cosa suya, la regla de esta linea es solo para ti. Al aprobar, esa copia se archiva en `aprobados/<id>.md` (historico); al rechazar se descarta sin dejar rastro. Las `sources` (ver `describe --sources`) son material de referencia del humano -- un Word con bibliografia, un `.tex` en Overleaf -- nunca las edites ni las "arregles", ni siquiera algo que parezca un error obvio: si hace falta un cambio ahi, dilo en la conversacion, no lo toques tu. Si eres Claude Code: `content/**` esta ademas denegado en `.claude/settings.json` -- si un `Edit`/`Write`/`Bash` te sale rechazado por permisos ahi, es intencional, no lo intentes rodear.
2. **Nunca pases `--authorized` sin que un humano te haya dado permiso explicito en la conversacion actual.** Aplica a `add-card --depends-on` y a `link` (para anadir una dependencia a una tarjeta ya existente). Pide permiso primero (di que dependencia quieres crear y por que), espera la respuesta, y solo entonces pasa `--authorized`.
3. **No hace falta pedir permiso** para: crear tarjetas sueltas (sin `--depends-on`), asignar tarjetas (`assign`), actualizar descripcion/sources (`describe`), proponer contenido (`propose`), o consultar estado (`status`, `diff`, `context`, `validate`).

## Estados (color de la tarjeta)

| Color | Preset | Estado | Que significa |
|---|---|---|---|
| morado | `"6"` | Backlog | lista para empezar |
| naranja | `"2"` | En progreso | asignada, trabajandose |
| rojo | `"1"` | Bloqueada | derivado del grafo -- no la asignes a mano, se recalcula sola |
| amarillo | `"3"` | Propuesta pendiente de revision | esperando `approve`/`reject` de un humano |
| cian | `"5"` | Solicitud cambio de dependencia | pendiente de autorizacion (regla 2) |
| verde | `"4"` | Aprobada | terminada |

## Arrancar un proyecto nuevo (vault sin tarjetas todavia)

Si `histos status` no muestra ninguna tarjeta, no te lances a crear el indice por tu cuenta. Antes de la primera `add-card`, pregunta al humano (es una conversacion normal, no hace falta CLI para esto):

1. **De que trata el proyecto** -- una frase basta, pero sin eso las tarjetas salen genericas.
2. **Que tipo de trabajo es**: experimental/investigacion, revision bibliografica, o mixto -- cambia bastante que va en cada tarjeta (sobre todo si hay una seccion tipo "Resultados"/"Metodologia").
3. **Si hay una estructura obligatoria** (plantilla de universidad, revista, organizacion) que seguir al pie de la letra, o si partimos de una estructura estandar para ese tipo de proyecto y la ajustamos juntos.
4. **Si tiene sentido anadir checkpoints** (revisiones periodicas con un tutor/editor/responsable) como tarjetas propias, no solo capitulos de contenido.

Con las respuestas, propon una tabla de tarjetas + dependencias (id, titulo, de que depende) y pide confirmacion explicita antes de crear nada con dependencias -- tarjetas sueltas no necesitan autorizacion (regla 3), pero en cuanto la tabla tenga una dependencia, si (regla 2). Una vez confirmada, crea las tarjetas en orden topologico.

## Comandos

Empieza siempre por `histos status` para saber que hay. Luego:

```
histos add-card <id> --title "..." [--description "..."] [--depends-on ID...] [--authorized]
histos link <id> --depends-on ID [ID...] --authorized   # anade dependencia a una tarjeta EXISTENTE
histos describe <id> [--text "..."] [--sources PATH...]  # solo frontmatter, no requiere autorizacion
histos assign <id> [id...] [--by agent|human]
histos context <id>                                      # descripcion+dependencias aprobadas+sources+PROJECT.md
histos propose <id> --file <borrador.md>
histos diff <id>
histos approve <id>
histos reject <id> [--feedback "..."]
histos validate
```

`histos <comando> --help` para el detalle de cada flag. Antes de proponer contenido para una tarjeta con dependencias, corre `histos context <id>` en vez de ir a leer cada `content/<dep>.md` a mano -- te junta todo (incluye ficheros externos que el humano haya registrado con `describe --sources`, por ejemplo un Word con bibliografia).

## Modo sin supervision (AFK)

Si un humano te asigna varias tarjetas y se va, puedes seguir trabajando la cola sin pedir permiso en cada paso: ningun comando de Histos bloquea en un prompt. El peor caso posible siguiendo las reglas de arriba es dejar tarjetas en amarillo esperando revision -- nunca contenido escrito sin permiso ni dependencias cambiadas sin autorizacion.
