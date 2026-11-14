Quiero construir un puente bidireccional entre una cuenta de WhatsApp y un chat de Telegram.

Quiero construir el puente como un bot de Telegram escrito en python, con los siguientes requerimientos:

# Requerimientos funcionales

- El bot debe conectarse a una cuenta única de WhatsApp (asociada a un número de telefono), utilizando la capacidad provista por la librería [neonize](https://github.com/krypton-byte/neonize)
- El bot debe monitorizar cualquier mensaje recibido, y capturar tanto el texto como cualquier contenido adjunto (imagenes, archivos, videos, etc.)
- Con el mensaje recibido, debe enviar el mensaje, tanto el texto como cualquier contenido adjunto, al usuario de Telegram con el mismo número de teléfono que la cuenta de WhatsApp. Para ello, durante el proceso de registro, el bot identificará y persistirá el chat id de Telegram de la conversación del bot con el usuario.
- El mensaje reenviado debe contener la identificación del remitente,  : `Remitente: <remitente>`. El remitente será el nombre del contacto si está disponible. Si no, indicar el número de teléfono.
- En el caso de que el mensaje sea un adjunto, el bot debe reenviar el adjunto al usuario de Telegram, añadiendo como cabecera el texto: `Fichero del remitente: <remitente>`
- El mensaje reenviado debe contener la identificación del mensaje original en WhatsApp.
- El bot debe monitorizar las respuestas que envíe el usuario a los mensajes enviados por el bot.
- En caso de que el usuario de Telegram responda a un mensaje del bot, el bot debe enviar dicha respuesta al usuario de WhatsApp. Para ello debe identificar el mensaje original, y responder al mismo.
- El código debe contener las siguientes salvaguardas:
  - Asegurar que no se produzcan bucles infinitos (que el bot capture como mensaje de WhatsApp a un mensaje que haya escrito el propio bot).
  - Respetar los límites del API público de Telegram en lo referido a tamaños de archivos adjuntos. Si no se puede enviar un archivo adjunto, se debe enviar el mensaje sin el adjunto con un texto adicional indicando: 'Fichero adjunto demasiado grande para ser reenviado'.
  - Debe estar protegido contra rearranques, para no reprocesar mensajes ya reenviados en caso de rearranque del script o del servicio



# Requerimientos técnicos

## Reglas generales de código y proyecto

- El bot debe ser escrito en python, utilizando la librería [neonize](https://github.com/krypton-byte/neonize).
- Para el tratamiento del API de Telegram, inicialmente se sugiere usar peticiones directas HTTP con urllib, pero se aceptan y revisar alternativas que nos recomienden.
- La versión canónica de python a utilizar es la 3.11.
- Se deben identificar cualquier requerimiento adicional para construir el archivo requirements.txt. Cualquier dependencia deberá validarse contra la versión de python seleccionada, antes de añadirla a dicho archivo.
- El estilo de código debe ser PEP8. Todo código python generado debe ser validado usando un linter antes de añadirlo al repositorio git o ejecutado.
- Los nombres de los artefactos (funciones, variables, clases, etc.) deben estar en inglés.
- Los comentarios del código deben estar en inglés.
- Los archivos de proyecto (README.md, CLAUDE.md, AGENTS.md, etc.) debe ser escritos en inglés, incluyendo este archivo
- El código se ejecutará en modo servicio sin fin. No debe contener interacción con el usuario ni en consola ni gráfica.
- El código deberá ser lo más sencillo y minimalista posible. No añadas código o funcionalidades no solicitadas. Es preferible añadir código de validación de parámetros que intentar soportar cada casuística atípica de uso. Esto no aplica a los comentarios, que deben estar orientados al mantenimiento posterior tanto por agentes AI como programadores humanos.

## Estructura de directorios

- El proyecto debe contener la siguiente estructura de directorios:

```txt
WTB_WhatsApp_Telegram_Bridge/
│
├── src/
│    └── wtb.py
|
├── scripts/
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── data/
│
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
└── README.md
```

## Logging

- El código utilizará las librerías de logging de python. No contendrá print() ni console.log(). Se identifican los siguientes niveles de log
  - INFO: Incluirá los mensajes reenviados, respuestas y cualquier otro mensaje de información general.
  - WARNING: Incluirá cualquier otro mensaje de advertencia. Específicamente, incluirá el mensaje indicando que está pendiente el registro de WhatsApp (incluyendo el código de 8 dígitos del registro). También incluirá cualquier mensaje truncado o que no haya podido reenviar los adjuntos por tamaño.
  - ERROR: Cualquier error producido por el bot. En este caso, se incluirá la traza completa del error.
  - DEBUG: Cualquier mensaje de desarrollo o depuración.
- Los log utilizarán un manejador que escriba en `stderr`
- Los logs nunca deben incluir el token de Telegram, credenciales de WhatsApp ni información sensible de autenticación.
- Los logs mostrarán el código de emparejamiento únicamente mientras el registro esté pendiente

## Configuración

- Cualquier parámetro para el archivo se leerá de las variables del entorno. Para el desarrollo se incluirá la carga de un archivo de entorno `.env`. En producción deberán ser variables existente en el entorno de producción. Durante el arranque se validará que las variables están definidas, y si no, se finalizará con mensaje de error y código de error 1.
- Inicialmente, los parámetros identificados son nivel de logging `LOG_LEVEL`, el token generado para el bot `TELEGRAM_BOT_TOKEN` y el número de teléfono `PHONE_NUMBER` en formato WhatsApp (IIXXXXXXXXX donde II es el prefijo internacional sin el signo +). Cualquier otro parámetro que sea necesario se añadirá en el futuro.

## Persistencia

- All persistent application data must be stored under data/.

- Neonize authentication and session data must be persisted
  using the storage mechanism provided by the library.

- Application state, Telegram registration and message
  processing records must be stored in a separate SQLite
  database managed by the application. 
  
- Currently we have identified that we need to persiste the following message data.

    | Dato               | Finalidad                           |
    | ------------------ | ----------------------------------- |
    |ID de registro.     |Identificador interno.               |
    |WhatsApp message ID |Identificar el mensaje original      |
    |WhatsApp chat JID   |Identificar la conversación de origen|
    |Telegram message ID |Identificar el mensaje reenviado     |
    |Telegram chat ID.   |Identificar el destino               |
    |Dirección           |Identificar el sentido del mensaje.  |
    |Estado              |Controlar el procesamiento           |
    |Fecha creación.     |Controlar el tiempo de vida          |
    |Fecha modificación  |Controlar el tiempo de vida          |

- Both storage locations must survive container recreation.




## Registro de WhatsApp

- El registro del código con neonize debe ser idempotente, esto es, si al arrancar aún no se ha registrado el bot, se producirá el registro con `PairPhone` mostrando el código de emparejamiento en la consola (stderr). Si el bot ya estaba registrado, utilizará los datos de registro existentes.

## Registro de Telegram (identificación del chat)


- The user must initiate a private conversation with the bot.
- The bot will request the user's phone number using a
  Telegram contact-sharing button (request_contact=True).
- The bot must verify that the shared contact belongs to
  the Telegram user who sent the message.
- The phone number will be normalized and compared with
  PHONE_NUMBER.
- If both numbers match, the bot will persist the Telegram
  chat_id in SQLite.
- If they do not match, the association will be rejected.
- Only one Telegram account can be associated with the
  configured WhatsApp account.
- Once registered, the bot will reuse the persisted chat_id.

## Tratamiento de errores

- Error de conexión
  - En situación de error de conexión (no se puede conectar a WhatsApp o Telegram), el bot emitirá un mensaje de error en el log y finalizará emitiendo un estado de error concreta.
  - Posteriormente, y de forma manual, el sistema podrá arrancar el bot, y este deberá resincronizarse usando la información persistida en la base de datos de estado y mensajes.
- Persistencia de mensajes: La persistencia de mensajes estará orientada a dos aspectos considerados críticos:
  - Garantizar que en caso de rearranque no se reprocesen mensajes ya reenviados
  - Permitir identificar la respuesta a mensajes reenviados, y responder al mismo mensaje

- Message processing must use a persistent state machine.
  - Each incoming WhatsApp message must be registered in SQLite
  before attempting delivery to Telegram.
  - The application must persist the Telegram message_id
  returned after a successful delivery.
  - Successfully delivered messages must never be intentionally
  processed again.
  - Messages left in an incomplete state after an unexpected
  restart must be handled according to a recovery policy.
  - The implementation must minimize duplicate deliveries.
  Exactly-once delivery across WhatsApp, Telegram and SQLite
  cannot be guaranteed.
  - Suggested message status codes: PENDING, PROCESSING, SENT y FAILED

- En caso de rearranque, cualquier mensaje que se encuentre en estado PROCESSING, generará un mensaje de error en el log, y se marcará como FAILED.

## Conversión de formatos

- La conversión de contenidos y APIs a utilizar se detallan en la siguiente tabla

| Tipo de contenido          | WhatsApp → Telegram                                                              | Respuesta Telegram → WhatsApp                                   |
| :------------------------- | :------------------------------------------------------------------------------- | :-------------------------------------------------------------- |
| **Texto**                  | HTML `<b>Remitente:</b> texto` con particionado si supera `4096` caracteres          | `texto`                                                         |
| **Fotos**                  | `wa_client.download_any` → `sendPhoto` (desbordamiento de *caption* si > `1024`) | `photo[-1]` → `wa_client.send_image`                            |
| **Vídeos / GIFs**          | `sendVideo` / `sendAnimation` según `gifPlayback`                                | `wa_client.send_video` (`gifplayback=True` si es animación)     |
| **Documentos**             | `sendDocument` conservando `fileName` y `mimetype`                               | `wa_client.send_document` conservando nombre y MIME             |
| **Notas de voz / Audios**  | `sendVoice` (si `PTT=True`) o `sendAudio`                                        | Etiqueta previa + `wa_client.send_audio(ptt=True/False)`        |
| **Stickers**               | `.webp` + mensaje de autor + `sendSticker`                                       | Estáticos/vídeo → `send_sticker`; `.tgs` → miniatura o etiqueta |
| **Ubicación / Contactos**  | Enlace Google Maps / `[Contacto: nombre]`                                        | Enlace Google Maps / etiqueta `[Contacto]`                      |
| **Respuestas (*Replies*)** | `contextInfo.stanzaID` → `reply_parameters` en Telegram                          | `build_quoted_wa_event` (`SimpleNamespace` compatible proto2)   |

- Cuando un mensaje de WhatsApp contiene un archivo adjunto y un texto que supera el límite de caption, se envía todo el texto en un mensajes independientes y posteriormente el adjunto.

## Pruebas

Las pruebas unitarias no deben requerir conexiones reales a WhatsApp ni Telegram.

Para ello, las APIs deben poder simularse mediante mocks.

Los casos de prueba incluirán:

- Mensajes duplicados.
- Reinicio durante el procesamiento.
- Mensajes sin contenido textual.
- Errores de envío.
- Respuestas a mensajes inexistentes.
- Adjuntos que superan los límites.
- Mensajes que no son respuestas.

# Plan de trabajo

## Fases funcionales

La construcción la realizaremos en las siguientes etapas funcionales

1. Producto mínimo viable: Registro en la cuenta de WhatsApp, y reenvío de los mensajes de texto.
2. Extensión al reenvío de mensajes con adjuntos.
3. Extensión a la captura de respuestas y reenvío de respuestas por WhatsApp.

## Fases de construcción para el producto mínimo viable

La construcción se iniciará por el producto mínimo viable, siguiendo las fases descritas a continuación. 

1. Fase inicial: 
    1. Código python en `wtb.py`, archivo `requirements.txt` y `README.md`
    2. Pruebas unitarias para la fase inicial. Scripts de pytest en carpeta individual `tests`.
    3. Base de datos SQLite en la carpeta `data` con estructura inicial.
2. Despliegue de la fase inicial
    1. Crear archivo Dockerfile para despliegue como contenedor
    2. Identificar la mejor imagen para el despliegue de la fase inicial.
    3. Confirmar que no se requiere root
    4. La carpeta `data`de persistencia se montará como un volumen externo para garantizar persistencia
    5. El código deberá poder capturar las señales SIGTERM, SIGINT para finalizar de forma ordenada cuando se solicite la terminación del contenedor, cerrando las conexiones y finalizar sin corromper el almacenamiento.
3. Normalización del código, separando las funcionalidades del script inicial `wtb.py`por módulo, con la siguiente estructura

```txt
├── src/
│   └── bridge/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── logging_config.py
│       ├── whatsapp.py
│       ├── telegram.py
│       ├── message_handler.py
│       └── storage.py
```

Para dar por finalizada cada fase, las condiciones de aceptación serán:

- El código debe funcionar.
- Las pruebas deben pasar.
- La documentación debe estar actualizada.
- No deben quedar funcionalidades parcialmente implementadas de fases posteriores.
- No debe romperse la funcionalidad de fases anteriores.

## Temas pendientes de decisión

- Tratamiento de los mensajes de WhatsApp con múltiples adjuntos
- Tratamiento de las respuestas de Telegram con múltiples adjuntos. Respuestas con álbumes.


## Fuera de alcance

- Tratamiento de los mensajes enviados al bot que no sean respuestas a mensajes recibidos previamente. Inicialmente, se ignorarán (reportando como un warning en el log que indique que el mensaje no se tratará)
- Conversaciones entre grupos. Este bridge sólo será entre cuentas individuales de WhatsApp y el chat del bot con el usuario en Telegram
- Mensajes salientes de WhatsApp por el usuario de forma manual. Se ignorarán y en su caso, se generá un registro en el log a nivel DEBUG
- Se ignoran los mensajes de estado de WhatsApp
- Mensajes editados en WhatsApp (no se envía actualización)
- Respuestas editadas en Telegram (no se envía actualización)
- Mensajes eliminados en WhatsApp (no se envía actualización o actualización)
