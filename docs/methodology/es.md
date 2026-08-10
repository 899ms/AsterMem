# Cómo funciona AsterMem

La mayoría de los productos de "memoria para IA" meten tus palabras en una caja negra: nunca sabes qué recordó, por qué, ni cuándo lo va a sacar a la luz. AsterMem toma otro camino: **tu memoria es primero tu activo, y solo después contexto para la IA.** Este documento explica cada decisión de diseño detrás del marco.

## 1. El texto original es la única verdad

Cada memoria se almacena como Markdown plano. Todo lo que genera la IA —resúmenes, etiquetas, tu perfil— es un **derivado** que puede reconstruirse desde la fuente en cualquier momento.

Esto no es purismo. Protege contra una vía de degradación fatal: **las paráfrasis de paráfrasis**. Un resumen es compresión con pérdida; si el sistema sigue resumiendo sus propios resúmenes, cada pasada se aleja más de lo que realmente escribiste, como fotocopiar una fotocopia hasta que las letras se desdibujan. Por eso AsterMem impone una restricción estricta: **toda llamada a la IA que produzca o reescriba una conclusión debe recibir el texto original como entrada.** Los artefactos intermedios son solo referencia.

Puedes editar los archivos MD con cualquier editor y el índice se sincroniza automáticamente. Tus datos nunca quedan encerrados en una base de datos: exportar es simplemente copiar una carpeta.

## 2. Recuperación en dos niveles: documentos y pasajes

En un material de memoria extenso, normalmente solo uno o dos párrafos son relevantes para la pregunta en cuestión. AsterMem divide automáticamente cada memoria en **pasajes (trunks)**, cada uno con su propio resumen, etiquetas y embedding. En el momento de la consulta:

- La **búsqueda por palabras clave** (índice de texto completo Whoosh) resuelve los aciertos exactos: nombres, proyectos, jerga
- La **búsqueda semántica** (vectores) resuelve la intención difusa: "¿qué dije que había que vigilar?"
- El **modo híbrido** fusiona ambas con RRF (Reciprocal Rank Fusion), con pesos dinámicos según las características de la consulta

La IA recibe resultados con precisión de pasaje, no documentos completos. Las ventanas de contexto son un recurso escaso: 500 palabras relevantes valen más que 5000 fuera de tema.

## 3. La recuperación es navegación, no preguntas y respuestas

Cada búsqueda devuelve más que resultados: devuelve **guía para el siguiente paso**: los ID de memorias semánticamente cercanas que no se mostraron, las etiquetas halladas en los aciertos, los documentos que vale la pena expandir. La IA no tiene que adivinar su próxima consulta; sigue los vínculos intrínsecos de tu grafo de memoria.

Esto imita la forma en que las personas rastrean su material de memoria: no te detienes en el primer resultado, sigues adelante con "aquello que mencionaba esta fuente".

## 4. Perfil: "quién es esta persona" en una sola llamada

Hacer que la IA aprenda desde cero quién eres en cada sesión es el desperdicio fundamental del chat sin estado. La capa de perfil de AsterMem destila todo tu almacén de memorias en un contexto denso que un agente recupera con una sola llamada a `get_profile`.

El perfil tiene tres capas de origen:

1. **Datos básicos**: campos estructurados como nombre, profesión y zona horaria. La IA los completa automáticamente a partir de tus memorias; puedes cambiar lo que quieras y, **una vez que editas un campo, la IA no vuelve a tocarlo jamás**. Cada cambio archiva el valor anterior en el historial de versiones.
2. **Tu propia presentación**: Markdown escrito por ti, entregado a la IA textualmente. Ninguna ruta de código del sistema puede modificarlo.
3. **Lo que la IA sabe**: observaciones destiladas de tus memorias, organizadas en rasgos a largo plazo, actividad reciente y un panorama de temas.

## 5. Cada frase escrita por la IA es rastreable

Toda conclusión que la IA escribe en tu perfil debe citar los ID de las memorias de origen. **Las afirmaciones no rastreables se descartan en la capa de análisis**: no se revisan y se eliminan, sencillamente nunca entran al sistema.

La generación y la revisión son dos llamadas independientes a la IA: primero se destilan conclusiones candidatas y luego un auditor verifica cada una contra el texto original: "¿la fuente realmente respalda esta afirmación?". Una retrospección diaria también rota por las conclusiones existentes: las fuentes eliminadas se marcan como "fuente no válida", las que llevan mucho tiempo sin verificarse como "posiblemente desactualizadas", y todo aterriza en una lista pendiente para que tú decidas. **El sistema nunca elimina en silencio, y nunca cree en silencio.**

## 6. Soñar: consolidación profunda de baja frecuencia

La destilación diaria solo ve el incremento de cada día; no puede detectar patrones que abarcan meses. AsterMem retoma la idea del "sueño" (consolidación fuera de línea) propuesta por investigadores de Anthropic: reexaminar periódicamente todo el almacén de memorias para eliminar duplicados, fusionar, resolver contradicciones e inducir temas de largo plazo.

El diseño clave: **la consolidación profunda nunca entra en vigor directamente.** Produce una versión candidata; tú revisas el diff (qué se agregó, qué se fusionó, qué se eliminó) y la activas o la descartas manualmente. La consolidación se activa por eventos —suficiente contenido nuevo acumulado, cuestiones pendientes que se amontonan, una importación masiva terminada—, no por un cron rígido. La gente no hace limpieza a fondo con horario fijo; limpia cuando ve el desorden.

La consolidación profunda tiene además un compañero ligero para el día a día: **el ordenado al escribir**. Cada vez que llega una memoria nueva, se sopesa frente a las memorias similares existentes: una decisión obsoleta queda reemplazada, un hecho ya registrado no se guarda dos veces. El ordenado solo archiva, nunca borra; cada decisión queda anotada con su razonamiento en el registro de mantenimiento, y todo lo archivado se recupera con un clic. Ante la duda, se conserva todo. Y si prefieres una biblioteca sin intervención, los resultados del sueño pueden aplicarse automáticamente — pero solo cuando cada conclusión supera la auditoría; cualquier punto dudoso sigue esperándote.

## 7. Visible, editable, desconectable

Un perfil es el resumen que la IA hace de ti: puede estar equivocado, puede ser parcial. Por eso el producto debe garantizar tres cosas:

- **Siempre visible**: "lo que ven los agentes" se muestra textualmente; no hay prompts ocultos
- **Siempre editable**: cada conclusión puede conservarse o eliminarse, cada campo puede reescribirse
- **Siempre desconectable**: el perfil está desactivado por defecto; apagado, no genera ninguna llamada a la IA y no cuesta nada

La confianza no se construye con promesas. Se construye con "puedes abrirlo y comprobarlo cuando quieras, y corregirlo con un clic".

## 8. Hecho para agentes

AsterMem no es una herramienta de documentos tradicional: es un **backend de memoria para agentes**:

- Una API de herramientas completa (búsqueda, lectura/escritura, perfil) con autenticación por token Bearer y niveles de permiso de lectura/escritura/destructivo
- Un paquete Skill incluido: Cursor, Claude Code y otros agentes lo instalan y listo
- `quick_match` devuelve en una sola llamada el contexto temporal + los pasajes más relevantes + la guía del siguiente paso, diseñado para la apertura de cada sesión
- `capture_conversation` permite al agente entregar una conversación completa: el texto se guarda literal, y lo que merece recordarse a largo plazo se destila en segundo plano en memorias independientes, cada una enlazada al original — guardar ya no depende de que el agente se acuerde de guardar
- Las respuestas de búsqueda operan bajo un presupuesto de caracteres y un límite de tiempo estricto: por grande que crezca la biblioteca, nunca detiene el turno del agente

Tú aportas el material de memoria. La IA recuerda quién eres. Eso es AsterMem.
