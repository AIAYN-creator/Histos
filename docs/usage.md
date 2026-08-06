# Guía de uso

Para el diseño completo ver el [README](../README.md); esto es una guía práctica de "cómo se usa esto en el día a día".

## Instalar

```bash
pip install -e /ruta/a/Trellis
```

(hasta que se publique en PyPI, instálalo apuntando a tu copia local del repo de Trellis — funciona desde cualquier directorio, no hace falta estar dentro de `Trellis/`.)

## Arrancar un proyecto

```bash
cd /ruta/a/tu/proyecto-de-escritura
trellis init
```

Esto crea `project.canvas`, `content/`, y las instrucciones para el agente (`AGENTS.md`, `CLAUDE.md`). Abre esa misma carpeta como vault en Obsidian — Canvas es una función nativa de Obsidian, no hace falta ningún plugin — y verás `project.canvas` como un tablero visual.

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

1. Le pides al agente (Claude Code, Codex, lo que uses) que trabaje una o varias tarjetas. El agente hace `trellis assign` y se pone a redactar.
2. El agente sube su propuesta con `trellis propose` — la tarjeta se pone amarilla. **No ha tocado el `.md` real todavía.**
3. Cuando tengas un rato, `trellis diff <id>` te enseña exactamente qué cambiaría (o abre el fichero de propuesta en `.trellis/proposals/` desde Obsidian).
4. Si te convence: `trellis approve <id>` — ahora sí se escribe el `.md` real. Si no: `trellis reject <id> --feedback "lo que le falta"` — el agente verá tu feedback la próxima vez que mire esa tarjeta.
5. `trellis status` en cualquier momento para ver el panorama completo.

## Por qué es seguro dejarlo trabajando solo

El agente **nunca** puede escribir en `content/*.md` sin pasar por los pasos 2-4 de arriba, y **nunca** puede tocar el grafo de dependencias sin pedírtelo primero en la conversación (regla que vive en `AGENTS.md`, y que el propio CLI hace cumplir con el flag `--authorized`). Puedes dejarlo procesando una cola de tarjetas sin estar presente: lo peor que te vas a encontrar al volver son varias tarjetas amarillas esperando revisión — nunca una sorpresa escrita sin tu permiso.

## Si algo se ve raro

```bash
trellis validate
```

Valida `project.canvas` contra el esquema formal y te dice exactamente qué está mal si algo se corrompió (referencias rotas, ciclos en las dependencias, tarjetas mal formadas).

## Referencia completa

Los 9 comandos con todos sus flags están en la sección [CLI del README](../README.md#cli). El esquema formal del `.canvas` está en [docs/canvas-schema.md](canvas-schema.md).
