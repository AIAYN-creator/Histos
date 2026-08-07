# Guía de uso

Para el diseño completo ver el [README](../README.md); esto es una guía práctica de "cómo se usa esto en el día a día".

## Instalar

```bash
pip install -e /ruta/a/Histos
```

(hasta que se publique en PyPI, instálalo apuntando a tu copia local del repo de Histos — funciona desde cualquier directorio, no hace falta estar dentro de `Histos/`.)

## Arrancar un proyecto

```bash
cd /ruta/a/tu/proyecto-de-escritura
histos init
```

Esto crea `project.canvas` (con una leyenda de colores ya puesta en el propio canvas), `content/`, y las instrucciones para el agente (`AGENTS.md`, `CLAUDE.md`). Abre esa misma carpeta como vault en Obsidian — Canvas es una función nativa de Obsidian, no hace falta ningún plugin — y verás el tablero visual con su leyenda arriba.

**Importante:** abre la carpeta que contiene `project.canvas` directamente como vault, no una carpeta por encima — si no, Obsidian no encuentra los `.md` de las tarjetas y las verás como "Create new note" en vez de con contenido.

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

- `histos describe <id> --text "..."` — pone o cambia la descripción de una tarjeta (una línea, aparece en `histos status` y en el frontmatter). No toca el contenido, así que no hace falta aprobarlo.
- `histos link <id> --depends-on ID [ID...] --authorized` — para cuando descubres una dependencia después de haber creado la tarjeta (si la sabías desde el principio, se pone directamente en `add-card --depends-on`).

## Si algo se ve raro

```bash
histos validate
```

Valida `project.canvas` contra el esquema formal y te dice exactamente qué está mal si algo se corrompió (referencias rotas, ciclos en las dependencias, tarjetas mal formadas).

## Referencia completa

Los 9 comandos con todos sus flags están en la sección [CLI del README](../README.md#cli). El esquema formal del `.canvas` está en [docs/canvas-schema.md](canvas-schema.md).
