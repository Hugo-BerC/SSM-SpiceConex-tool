# SpiceConex v1.0.0

## Novedades y mejoras

- Nueva pestaña SRE **GlobalInventory**: inventario multicuenta desde AWS Config
  Aggregator, filtros y exportación JSON/CSV para contrastar con la CMDB.
- Identificadores, ARN y nombres separados; enriquecimiento con el tag `Name`
  cuando AWS Config lo proporciona. Estado y recuentos visibles en informes
  parciales; la cobertura depende de lo que registre el agregador.
- Actualizador con validación de repositorio, SHA y fast-forward, entorno de
  dependencias separado y recuperación ante fallos.
- Performance ejecuta consultas y análisis en segundo plano; confirma la creación
  de dashboards y comprueba colisiones de nombres antes de publicarlos.
- Clientes AWS con timeouts, retries y errores compartidos; recuperación SSO,
  paginación y seguimiento de comandos reforzados.
- Instalación y arranque estabilizados: dependencias unificadas, rutas por
  plataforma, perfiles AWS filtrados y logos opcionales.
- Componentes de interfaz extraídos de forma incremental; logs con historial,
  identificadores de tareas y redacción de patrones sensibles.

## Validación

- 265 pruebas locales superadas en Windows x64 con Python 3.14.
- Instalación limpia, construcción/instalación del paquete y arranque de la ventana
  real en modo demo sin logos verificados en Windows.
- La publicación automática exige superar la matriz de CI en Windows, macOS y
  Linux con Python 3.10, 3.12 y 3.14.
- Los instaladores completos de macOS/Linux y las operaciones contra AWS real no
  se han validado en este entorno. La suite utiliza mocks/stubs de AWS.

## Instalación y actualización

Consulta [README.md](https://github.com/Hugo-BerC/SSM-SpiceConex-tool/blob/v1.0.0/README.md).
Desde una versión anterior sin el selector de runtime, actualiza el checkout y
ejecuta de nuevo `setup.bat` en Windows o `./setup.sh` en Linux/macOS. Conserva la
configuración local de AWS existente.
