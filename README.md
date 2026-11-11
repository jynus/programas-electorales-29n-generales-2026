# Programas electorales: elecciones generales del 29 de noviembre de 2026

Programas electorales convertidos de su PDF original a reStructuredText, con el texto verificado frente al PDF.

| Partido | Programa | Idioma | Texto (RST) | PDF original | Versión | Páginas | Palabras |
|---|---|---|---|---|---|---:|---:|
| Adelante Andalucía | Un programa para frenar a las derechas | castellano | [adelante_andalucia.rst](programas_electorales/adelante_andalucia.rst) | [programa-29n-adelante-andalucia.pdf](documentos_originales/programa-29n-adelante-andalucia.pdf) ([fuente](https://programa.adelanteandalucia.org/programa-29n-adelante-andalucia.pdf)) | 1 | 134 | 31.742 |
| Bloque Nacionalista Galego (BNG) | PROGRAMA ELECTORAL | gallego | [bng.rst](programas_electorales/bng.rst) | [26_bng_xerais_documentos_programa_electoral.pdf](documentos_originales/26_bng_xerais_documentos_programa_electoral.pdf) ([fuente](https://www.bng.gal/media/bnggaliza/files/2026/11/03/26_bng_xerais_documentos_programa_electoral.pdf)) | 1 | 91 | 25.684 |
| Coalición Canaria y Municipalistas | DEFIENDE CANARIAS | castellano | [coalicion_canaria.rst](programas_electorales/coalicion_canaria.rst) | [CCA-26-Programa_Electoral-29NOV.pdf](documentos_originales/CCA-26-Programa_Electoral-29NOV.pdf) ([fuente](https://coalicioncanaria.org/wp-content/uploads/2026/11/CCA-26-Programa_Electoral-29NOV.pdf)) | 1 | 23 | 9.239 |
| Esquerra Republicana (ERC) | Eleccions al Congrés 2026 | catalán | [erc.rst](programas_electorales/erc.rst) | [e2026-programa.pdf](documentos_originales/e2026-programa.pdf) ([fuente](https://esquerrarepublicana.cat/documents/e2026-programa.pdf)) | 1 | 48 | 25.310 |
| Partido Humanista | ELECCIONES GENERALES 2026 | castellano | [partido_humanista.rst](programas_electorales/partido_humanista.rst) | [Programa Generales 2026.pdf](documentos_originales/Programa%20Generales%202026.pdf) ([fuente](https://partidohumanista.es/Documentos/Programas/Programa%20Generales%202026.pdf)) | 1 | 31 | 7.875 |
| EAJ-PNV | PROGRAMA ELECCIONES GENERALES 2026 | castellano | [pnv.rst](programas_electorales/pnv.rst) | [programa-electoral-29n.pdf](documentos_originales/programa-electoral-29n.pdf) ([fuente](https://www.eaj-pnv.eus/es/adjuntos-documentos/21765/pdf/programa-electoral-29n)) | 1 | 43 | 11.267 |
| Por un Mundo Más Justo (M+J) | PROGRAMA ELECTORAL · 29N 2026 | castellano | [por_un_mundo_mas_justo.rst](programas_electorales/por_un_mundo_mas_justo.rst) | [Castellano.pdf](documentos_originales/Castellano.pdf) ([fuente](https://www.porunmundomasjusto.es/wp-content/uploads/2026/11/Castellano.pdf)) | 1 | 95 | 19.687 |
| Partido Popular (PP) | RECONSTRUCCIÓN NACIONAL | castellano | [pp.rst](programas_electorales/pp.rst) | [LA-RECONSTRUCCION-NACIONAL-1.pdf](documentos_originales/LA-RECONSTRUCCION-NACIONAL-1.pdf) ([fuente](https://www.pp.es/wp-content/uploads/2026/11/LA-RECONSTRUCCION-NACIONAL-1.pdf)) | 1 | 82 | 23.599 |
| Se Acabó La Fiesta (SALF) | CONTRATO ELECTORAL | castellano | [salf.rst](programas_electorales/salf.rst) | [SALF_Contrato_Electoral_29N.pdf](documentos_originales/SALF_Contrato_Electoral_29N.pdf) ([fuente](https://www.seacabolafiesta.com/SALF_Contrato_Electoral_29N.pdf)) | 1 | 40 | 8.598 |

## Sumas de verificación de los originales

| Fichero | SHA-256 |
|---|---|
| [programa-29n-adelante-andalucia.pdf](documentos_originales/programa-29n-adelante-andalucia.pdf) | `223fcb661c92858f1425cdbf8d671673c89a23c5952cbc232fed02e49d2d4f0f` |
| [26_bng_xerais_documentos_programa_electoral.pdf](documentos_originales/26_bng_xerais_documentos_programa_electoral.pdf) | `fd5aef260b9c66d030b57b7b0e18914c8ad79384e2c46b2346d9fb4f6514a97d` |
| [CCA-26-Programa_Electoral-29NOV.pdf](documentos_originales/CCA-26-Programa_Electoral-29NOV.pdf) | `3c40c0a6e38d8dd80ae1061d94b614f4e3f1b6492ec8c801834fb497f337044e` |
| [e2026-programa.pdf](documentos_originales/e2026-programa.pdf) | `61728d162a6e88d492d44a0b0994effb31f9e4abb85ebf8aea715d870a9b2e04` |
| [Programa Generales 2026.pdf](documentos_originales/Programa%20Generales%202026.pdf) | `c53ab576b0c57a091665795cb8676269b85cc6f9b7e22c9d65ce11d86502086f` |
| [programa-electoral-29n.pdf](documentos_originales/programa-electoral-29n.pdf) | `5af7a5b6f67c26bad87ef6c3ca01ae38aa1f1d7d9029efb32fc52482de0c1314` |
| [Castellano.pdf](documentos_originales/Castellano.pdf) | `a3c7c807411d8565b310a9b566c6fa2cbe643e99c91f3f9f4e7d8712d949c613` |
| [LA-RECONSTRUCCION-NACIONAL-1.pdf](documentos_originales/LA-RECONSTRUCCION-NACIONAL-1.pdf) | `cea8f2a052278e51ad682d19444eb0e9089997adbde8df76babf7f236bb5ffbe` |
| [SALF_Contrato_Electoral_29N.pdf](documentos_originales/SALF_Contrato_Electoral_29N.pdf) | `073425b9522da77608a0399923c2e0d3b9153776e8a0436ac8f0aea794b7893b` |

## Cómo se generan

Todo se regenera con `python3 scripts/build.py`, que lee `documentos_originales/latest.json`, comprueba el SHA-256 de cada PDF (también frente a `documentos_originales/SHA256SUM.txt`), ejecuta `scripts/<partido>-pdf2rst.py` y verifica el resultado antes de escribir `programas_electorales/` y este README:

* cada conversor compara su RST con el texto del PDF y falla ante cualquier texto añadido, omitido o alterado;
* una comprobación independiente lee el RST con pandoc y exige que cada palabra proceda del texto del PDF (`pdftotext`); solo se permiten cambios de espacios, unión de palabras cortadas a final de línea, bloques movidos sin cambios y la omisión de elementos de página (números de página, cabeceras y pies);
* `python3 scripts/build.py --check` falla si algún fichero de `programas_electorales/` no coincide con lo que se genera a partir de los originales.

Este fichero se genera automáticamente; no lo edites a mano.
