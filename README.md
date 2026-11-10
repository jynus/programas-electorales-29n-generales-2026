# Programas electorales: elecciones generales del 29 de noviembre de 2026

Programas electorales convertidos de su PDF original a reStructuredText, con el texto verificado frente al PDF.

| Partido | Programa | Texto (RST) | PDF original | Versión | Páginas | Palabras |
|---|---|---|---|---|---:|---:|
| Partido Popular (PP) | RECONSTRUCCIÓN NACIONAL | [pp.rst](programas_electorales/pp.rst) | [LA-RECONSTRUCCION-NACIONAL-1.pdf](documentos_originales/LA-RECONSTRUCCION-NACIONAL-1.pdf) ([fuente](https://www.pp.es/wp-content/uploads/2026/11/LA-RECONSTRUCCION-NACIONAL-1.pdf)) | 1 | 82 | 23.599 |
| Adelante Andalucía | Un programa para frenar a las derechas | [adelante_andalucia.rst](programas_electorales/adelante_andalucia.rst) | [programa-29n-adelante-andalucia.pdf](documentos_originales/programa-29n-adelante-andalucia.pdf) ([fuente](https://programa.adelanteandalucia.org/programa-29n-adelante-andalucia.pdf)) | 1 | 134 | 31.743 |
| Bloque Nacionalista Galego (BNG) | PROGRAMA ELECTORAL | [bng.rst](programas_electorales/bng.rst) | [26_bng_xerais_documentos_programa_electoral.pdf](documentos_originales/26_bng_xerais_documentos_programa_electoral.pdf) ([fuente](https://www.bng.gal/media/bnggaliza/files/2026/11/03/26_bng_xerais_documentos_programa_electoral.pdf)) | 1 | 91 | 25.684 |
| Coalición Canaria y Municipalistas | DEFIENDE CANARIAS | [coalicion_canaria_y_municipalistas.rst](programas_electorales/coalicion_canaria_y_municipalistas.rst) | [CCA-26-Programa_Electoral-29NOV.pdf](documentos_originales/CCA-26-Programa_Electoral-29NOV.pdf) ([fuente](https://coalicioncanaria.org/wp-content/uploads/2026/11/CCA-26-Programa_Electoral-29NOV.pdf)) | 1 | 23 | 9.239 |

## Sumas de verificación de los originales

| Fichero | SHA-256 |
|---|---|
| [LA-RECONSTRUCCION-NACIONAL-1.pdf](documentos_originales/LA-RECONSTRUCCION-NACIONAL-1.pdf) | `cea8f2a052278e51ad682d19444eb0e9089997adbde8df76babf7f236bb5ffbe` |
| [programa-29n-adelante-andalucia.pdf](documentos_originales/programa-29n-adelante-andalucia.pdf) | `223fcb661c92858f1425cdbf8d671673c89a23c5952cbc232fed02e49d2d4f0f` |
| [26_bng_xerais_documentos_programa_electoral.pdf](documentos_originales/26_bng_xerais_documentos_programa_electoral.pdf) | `fd5aef260b9c66d030b57b7b0e18914c8ad79384e2c46b2346d9fb4f6514a97d` |
| [CCA-26-Programa_Electoral-29NOV.pdf](documentos_originales/CCA-26-Programa_Electoral-29NOV.pdf) | `3c40c0a6e38d8dd80ae1061d94b614f4e3f1b6492ec8c801834fb497f337044e` |

## Cómo se generan

Todo se regenera con `python3 build.py`, que lee `documentos_originales/latest.json`, comprueba el SHA-256 de cada PDF (también frente a `documentos_originales/SHA256SUM.txt`), ejecuta `scripts/<partido>-pdf2rst.py` y verifica el resultado antes de escribir `programas_electorales/` y este README:

* cada conversor compara su RST con el texto del PDF y falla ante cualquier texto añadido, omitido o alterado;
* una comprobación independiente lee el RST con pandoc y exige que cada palabra proceda del texto del PDF (`pdftotext`); solo se permiten cambios de espacios, unión de palabras cortadas a final de línea, bloques movidos sin cambios y la omisión de elementos de página (números de página, cabeceras y pies);
* `python3 build.py --check` falla si algún fichero de `programas_electorales/` no coincide con lo que se genera a partir de los originales.

Este fichero se genera automáticamente; no lo edites a mano.
