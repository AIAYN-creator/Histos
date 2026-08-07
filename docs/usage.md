# Guía de uso

Para el diseño completo ver el [README](../README.md); esto es una guía práctica de "cómo se usa esto en el día a día".

## Instalar

```bash
pip install -e /ruta/a/Histos
```

(hasta que se publique en PyPI, instálalo apuntando a tu copia local del repo de Histos — funciona desde cualquier directorio, no hace falta estar dentro de `Histos/`.)

## Arrancar un proyecto

Probado de verdad (2026-08-07): una sesión de agente completamente nueva, sin nada de contexto previo sobre el proyecto ni sobre Histos, siguió estos pasos sin un solo problema.

1. **Crea una carpeta nueva** para el proyecto, distinta de cualquier vault existente — p. ej. `C:\Users\Usuario\Projects\MiProyecto`.

2. **Corre `histos init` tú mismo**, en una terminal normal, *antes* de abrir el agente:

   ```bash
   cd /ruta/a/tu/proyecto-de-escritura
   histos init
   ```

   Esto crea `project.canvas` (con la leyenda de colores ya puesta), `content/`, y las instrucciones para el agente (`AGENTS.md`, `CLAUDE.md`). El orden importa: hasta que estos ficheros existen, un agente nuevo no tiene forma de saber que esto es un proyecto Histos.

3. **Abre esa misma carpeta como vault en Obsidian** — Canvas es una función nativa, no hace falta ningún plugin — y verás el tablero con la leyenda arriba. **Importante:** la carpeta que abras tiene que ser exactamente la que contiene `project.canvas`, nunca una por encima — si no, Obsidian no encuentra los `.md` de las tarjetas y las verás como "Create new note" en vez de con contenido.

4. **Abre una sesión de agente nueva** (ventana/conversación distinta) con esa carpeta como directorio de trabajo. Claude Code carga `CLAUDE.md` automáticamente al arrancar, que a su vez importa `AGENTS.md` entero — no hace falta que le expliques nada sobre Histos.

5. **Dile de qué trata el proyecto**, sin más — algo natural tipo "quiero organizar mi TFG sobre X" o "ayúdame a montar este proyecto de escritura". Si `AGENTS.md` está haciendo su trabajo, el agente te pregunta de qué trata, si es experimental/revisión bibliográfica/mixto, si hay una plantilla obligatoria, y si conviene meter checkpoints de revisión — y con eso propone una tabla de tarjetas y dependencias, que puedes ajustar antes de confirmar. Si se lanza directo a crear tarjetas genéricas sin preguntar nada, pídeselo tú explícitamente (y avisa, porque significa que `AGENTS.md` necesita un repaso).

6. **Refresca Obsidian (`Ctrl+R`) después de que el agente cree las tarjetas.** Igual que con los ficheros de contenido, el canvas se edita por fuera de Obsidian (vía el CLI) y Obsidian no siempre se entera solo de que `project.canvas` cambió mientras lo tenías abierto.

## Leer el tablero

El color de cada tarjeta es su estado:

| Color | Estado |
|---|---|
| morado | Backlog — pendiente |
| naranja | En progreso |
| rojo | Bloqueada (se recalcula sola, no la toques a mano) |
| amarillo | Propuesta pendiente — **te toca revisarla** |
| cian | El agente pide autorización para tocar una dependencia — **te toca decidir** |
| verde | Aprobada |

Cuando veas una tarjeta amarilla o cian, es tu turno.

## El ciclo del día a día

1. Le pides al agente (Claude Code, Codex, lo que uses) que trabaje una o varias tarjetas. El agente hace `histos assign` y se pone a redactar.
2. El agente sube su propuesta con `histos propose` — la tarjeta se pone amarilla. **No ha tocado el `.md` real todavía.**
3. Cuando tengas un rato, `histos diff <id>` te enseña exactamente qué cambiaría — o simplemente abre `propuestas/<id>.md` en Obsidian como cualquier otra nota (es una carpeta visible, no oculta: puedes verla, leerla, e incluso retocarla a mano antes de aprobar).
4. Si te convence: `histos approve <id>` — ahora sí se escribe el `.md` real, y una copia de lo aprobado se archiva en `aprobados/<id>.md` (por si luego quieres comparar o te arrepientes de una aprobación rápida). Si no: `histos reject <id> --feedback "lo que le falta"` — se descarta sin dejar rastro (lo rechazado no se archiva, no hace falta) y el agente verá tu feedback la próxima vez que mire esa tarjeta.
5. `histos status` en cualquier momento para ver el panorama completo.

## Por qué es seguro dejarlo trabajando solo

El agente **nunca** puede escribir en `content/*.md` sin pasar por los pasos 2-4 de arriba, y **nunca** puede tocar el grafo de dependencias sin pedírtelo primero en la conversación (regla que vive en `AGENTS.md`, y que el propio CLI hace cumplir con el flag `--authorized`). Puedes dejarlo procesando una cola de tarjetas sin estar presente: lo peor que te vas a encontrar al volver son varias tarjetas amarillas esperando revisión, con sus borradores completos ya visibles en `propuestas/` — nunca una sorpresa escrita sin tu permiso.

## Otros comandos útiles

- `histos describe <id> [--text "..."] [--sources ruta1 ruta2 ...]` — pone o cambia la descripción y/o la lista de ficheros de referencia externos de una tarjeta (`.txt`, `.md`, `.tex`, `.docx` — por ejemplo el Word donde llevas la bibliografía, o el `.tex` que estás editando en VSCode/Overleaf). `--sources` sustituye la lista entera, no añade. No toca el contenido, así que no hace falta aprobarlo.
- `histos context <id>` — junta en un solo bloque de texto: la descripción y sources de la tarjeta, lo mismo de cada dependencia directa (más su contenido si ya está Aprobada), y `PROJECT.md` si existe. Pensado para que el agente lo corra antes de ponerse a redactar, en vez de ir a buscar cada pieza a mano.
- `histos link <id> --depends-on ID [ID...] --authorized` — para cuando descubres una dependencia después de haber creado la tarjeta (si la sabías desde el principio, se pone directamente en `add-card --depends-on`).

## Si algo se ve raro

```bash
histos validate
```

Valida `project.canvas` contra el esquema formal y te dice exactamente qué está mal si algo se corrompió (referencias rotas, ciclos en las dependencias, tarjetas mal formadas).

## Referencia completa

Los 12 comandos con todos sus flags están en la sección [CLI del README](../README.md#cli). El esquema formal del `.canvas` está en [docs/canvas-schema.md](canvas-schema.md).
