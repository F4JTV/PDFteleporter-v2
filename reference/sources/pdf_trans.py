# Source Generated with Decompyle++
# File: pdf_trans.pyc (Python 3.13)

'''
================================================================================
pdf_trans.py - Bibliothèque de transfert PDF par radio pour TCQ
================================================================================
Auteur  : F1GBD / ADRASEC 77 - FNRASEC
Date    : Mai 2026
Version : 1.0.5

Historique :
  1.0.5 (mai 2026)  — Correctif "cases à cocher perdues" sur les PDF
                      formulaires (AcroForm). Les champs Texte remplis
                      survivaient déjà (leur valeur est gravée dans le flux
                      d\'apparence que get_text restitue en spans de texte),
                      mais la COCHE des cases à cocher / boutons radio vit
                      dans l\'apparence /AP /N du widget, que ni get_text ni
                      get_drawings n\'extraient : elle disparaissait donc du
                      PDF recomposé (typique des SITREP ADRASEC remplis).
                      _extract_page_struct relève désormais la position des
                      cases COCHÉES (nouvelle clé "ck" du manifeste) et
                      _rebuild_struct_mode y redessine une coche vectorielle
                      (tracé géométrique pur, sans dépendance de police). Le
                      cadre vide de la case provient déjà du contenu de page.
                      Empreinte négligeable dans l\'archive et compatibilité
                      ascendante totale (les archives < 1.0.5 n\'ont pas la
                      clé "ck" ; un décodeur antérieur ignore cette clé).
  1.0.4 (mai 2026)  — Correctif de l\'interligne sur les PDF à fontes très
                      petites. La v1.0.3 utilisait un minimum absolu de 12 pt
                      pour la hauteur du rect de rendu, ce qui chevauchait
                      les lignes successives sur les PDF Excel à fontes ~4 pt
                      (espacement réel ~5.7 pt entre baselines). La v1.0.4
                      utilise une hauteur proportionnelle à la taille réelle
                      des polices de chaque ligne (line_height * 1.15 ou
                      fontsize * 1.20, le maximum des deux), ce qui résout
                      l\'empilement vertical observé dans les cadres
                      "Commentaires" des SITREP Excel. Compatibilité
                      ascendante totale.
  1.0.3 (mai 2026)  — Deux correctifs :
                      1) **Ligatures LibreOffice** : décomposition automatique
                         des ligatures Unicode (ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ) et des
                         caractères de ligature non standard utilisés par
                         LibreOffice (Ɵ U+019F = "ti", Ʃ U+01A9 = "tt"). Ces
                         caractères apparaissaient comme carrés vides ou
                         � de remplacement à la recomposition.
                      2) **Débordement avec petites fontes (Excel)** :
                         élargissement minimal du rect de rendu (0.5 pt de
                         chaque côté) pour compenser la marge interne par
                         défaut d\'insert_htmlbox, et CSS explicite pour
                         neutraliser margin/padding. scale_low ajusté à 0.5
                         pour permettre une réduction modérée sans tronquer.
                      Compatibilité ascendante préservée.
  1.0.2 (mai 2026)  — Correctif "débordement de texte" dans la recomposition
                      structurée : respect strict du bounding box d\'origine des
                      lignes de texte (au lieu de l\'étirer jusqu\'au bord de la
                      page), évite les textes qui sortent des cellules des
                      tableaux colorés. La famille de fonte d\'origine
                      (sans-serif / serif / mono) est désormais conservée dans
                      l\'archive pour un rendu plus fidèle. Compatibilité
                      ascendante préservée avec les archives v1.0.1 et v1.0.0.
  1.0.1 (mai 2026)  — Correctif "fond noir" : itération sur les sub-items des
                      paths vectoriels au lieu d\'utiliser le bounding box
                      global (cf. _extract_page_struct), pour éliminer les
                      gros rectangles noirs pleine page générés à partir des
                      encadrements multi-segments produits par les PDF
                      "Microsoft: Print To PDF" et "Microsoft Word LTSC".
  1.0.0 (mars 2026) — Version initiale publique.

Bibliothèque de fonctions pour la compression, transmission et recomposition
de documents PDF (PSDI, ORSEC, etc.) via les modes radio de TCQ :
  - TNC Packet (Direwolf KISS / TNC série) via fragmentation APRS
  - VARA (HF/FM/SAT) via trames binaires robustes

Architecture:
  pdf_trans.py  (cette librairie - fonctions autonomes, pas de dépendance GUI)
       ↕
  TCQ.py        (appelle les fonctions, gère l\'interface et les connexions radio)

Fonctions principales:
  - pdf_to_archive()      : PDF → archive .psdi compressée (émission)
  - archive_to_pdf()      : archive .psdi → PDF recomposé (réception)
  - prepare_for_tnc()     : archive → fragments TNC Packet (base64 + headers APRS)
  - reassemble_from_tnc() : fragments TNC reçus → archive
  - prepare_for_vara()    : archive → trame VARA binaire robuste
  - decode_from_vara()    : trame VARA reçue → archive
  - get_transfer_info()   : informations et estimations temps de transmission

Dépendances: fitz (PyMuPDF), Pillow, zlib, lzma, struct (stdlib)
================================================================================
'''
import os
import io
import sys
import json
import time
import zlib
import lzma
import struct
import base64
import hashlib
import logging
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Callable, Any

try:
    import fitz
    FITZ_AVAILABLE = True
    
    try:
        from PIL import Image
        PIL_AVAILABLE = True
        PDF_TRANS_VERSION = '1.0.6'
        ARCHIVE_MAGIC = b'PSDI'
        ARCHIVE_VERSION_STRUCT = 1
        ARCHIVE_VERSION_IMAGE = 2
        QUALITY_HIGH = 'high'
        QUALITY_MEDIUM = 'medium'
        QUALITY_LOW = 'low'
        QUALITY_ULTRA_LOW = 'ultra_low'
        QUALITY_PRESETS = {
            QUALITY_ULTRA_LOW: {
                'dpi': 72,
                'jpeg_quality': 20,
                'img_max_dim': 400,
                'map_max_dim': 300,
                'banner_max_h': 25,
                'lzma_preset': 6,
                'description': 'Urgence (Packet 1200 baud)' },
            QUALITY_LOW: {
                'dpi': 90,
                'jpeg_quality': 30,
                'img_max_dim': 600,
                'map_max_dim': 400,
                'banner_max_h': 30,
                'lzma_preset': 9,
                'description': 'Qualité réduite (Packet 9600 / VARA HF lent)' },
            QUALITY_MEDIUM: {
                'dpi': 120,
                'jpeg_quality': 45,
                'img_max_dim': 800,
                'map_max_dim': 600,
                'banner_max_h': 40,
                'lzma_preset': 9,
                'description': 'Qualité moyenne (VARA HF / FM)' },
            QUALITY_HIGH: {
                'dpi': 150,
                'jpeg_quality': 55,
                'img_max_dim': 900,
                'map_max_dim': 700,
                'banner_max_h': 50,
                'lzma_preset': 9,
                'description': 'Haute qualité (VARA FM rapide)' } }
        TNC_MAX_PAYLOAD = 170
        TNC_FRAME_TYPE_CODE = 'P'
        TNC_INTER_FRAME_DELAY = 0.5
        VARA_FRAME_TYPE = 'VPDF'
        RADIO_BITRATES = {
            'VARA HF (2400 bps)': 2400,
            'VARA HF turbo (4800 bps)': 4800,
            'VARA FM narrow (9600 bps)': 9600,
            'VARA FM wide (25000 bps)': 25000,
            'Packet 9600 bps': 9600,
            'Packet 1200 bps': 1200,
            'ARDOP (2000 bps)': 2000,
            'Winlink VARA HF': 3200,
            'LoRa (~1200 bps)': 1200 }
        logger = logging.getLogger('pdf_trans')
        
        def pdf_to_archive(pdf_path, quality = QUALITY_MEDIUM, mode = 'struct', output_path = None, progress_callback = None, skip_images = False):
            '''
Convertit un PDF en archive .psdi compressée pour transmission radio.

Args:
    pdf_path:  Chemin vers le fichier PDF source
    quality:   Préréglage qualité (high, medium, low, ultra_low)
    mode:      "struct" (texte+images, compact, défaut) ou "image" (rendu page)
    output_path: Chemin optionnel pour sauvegarder l\'archive sur disque
    progress_callback: Fonction (step, total, message) pour le suivi
    skip_images: Si True, n\'inclut pas les images (mode Fast, texte seul)

Returns:
    Tuple (archive_bytes, info_dict)
    - archive_bytes : bytes de l\'archive .psdi
    - info_dict     : dict avec métadonnées (taille, ratio, estimations, etc.)

Raises:
    RuntimeError: si PyMuPDF ou Pillow non disponibles
    FileNotFoundError: si le PDF n\'existe pas
    ValueError: si quality ou mode invalide
'''
            _check_dependencies()
            if not os.path.exists(pdf_path):
                pass
            raise FileNotFoundError(f'''PDF introuvable: {pdf_path}''')
            if quality not in QUALITY_PRESETS:
                pass
            raise ValueError(f'''Qualité invalide: {quality}. Choix: {', '.join(QUALITY_PRESETS.keys())}''')
            if mode not in ('image', 'struct'):
                pass
            raise ValueError(f'''Mode invalide: {mode}. Choix: image, struct''')
            params = QUALITY_PRESETS[quality]
            orig_size = os.path.getsize(pdf_path)
            t0 = time.time()
            auto_switch_reason = None
            if not mode == 'struct' and skip_images:
                (detected_mode, auto_switch_reason) = _detect_optimal_mode(pdf_path)
                if detected_mode != mode:
                    logger.info(f'''[pdf_trans] Auto-bascule struct→image (raison: {auto_switch_reason})''')
            mode = detected_mode
            _progress(progress_callback, 0, 3, f'''Ouverture PDF ({orig_size // 1024} Ko)...''')
            _progress(progress_callback, 3, 3, 'Compression terminée')
            elapsed = time.time() - t0
            if output_path:
                f = open(output_path, 'wb')
                f.write(archive_bytes)
            None if mode == 'image' else None(None, None)
            info = {
                'source': os.path.basename(pdf_path),
                'original_size': orig_size,
                'archive_size': len(archive_bytes),
                'ratio_percent': round((len(archive_bytes) / orig_size) * 100, 1),
                'compression_time': round(elapsed, 2),
                'quality': quality,
                'mode': mode,
                'auto_mode_switch': auto_switch_reason,
                'estimates': _estimate_times(len(archive_bytes)) }
            logger.info(f'''[pdf_trans] PDF→archive: {orig_size:,}→{len(archive_bytes):,} octets ({info['ratio_percent']}%) en {elapsed:.1f}s [{quality}/{mode}]''')
            return (archive_bytes, info)
            with None:
                if not None:
                    pass
            continue

        
        def archive_to_pdf(archive_data, output_path = None, progress_callback = None):
            """
Recompose un PDF à partir d'une archive .psdi reçue par radio.

Args:
    archive_data:  Bytes de l'archive .psdi
    output_path:   Chemin optionnel pour sauvegarder le PDF
    progress_callback: Fonction (step, total, message) pour le suivi

Returns:
    Tuple (pdf_bytes, info_dict)
    - pdf_bytes : bytes du PDF recomposé
    - info_dict : dict avec métadonnées

Raises:
    ValueError: si l'archive est invalide ou corrompue
"""
            _check_dependencies()
            if len(archive_data) < 6 or archive_data[:4] != ARCHIVE_MAGIC:
                pass
            raise ValueError('Archive invalide: signature PSDI manquante')
            version = struct.unpack_from('<H', archive_data, 4)[0]
            _progress(progress_callback, 0, 2, "Décompression de l'archive...")
            if version == ARCHIVE_VERSION_IMAGE:
                (pdf_bytes, info) = _rebuild_image_mode(archive_data, progress_callback)
            elif version == ARCHIVE_VERSION_STRUCT:
                (pdf_bytes, info) = _rebuild_struct_mode(archive_data, progress_callback)
            else:
                raise ValueError(f'''Version d\'archive non supportée: {version}''')
            _progress(progress_callback, 2, 2, 'PDF recomposé')
            if output_path:
                f = open(output_path, 'wb')
                f.write(pdf_bytes)
                None(None, None)
                info['output_path'] = output_path
            logger.info(f'''[pdf_trans] Archive→PDF: {len(archive_data):,}→{len(pdf_bytes):,} octets''')
            return (pdf_bytes, info)
            with None:
                if not None:
                    pass
            continue

        
        def prepare_for_tnc(archive_data, max_payload = TNC_MAX_PAYLOAD):
            """
Prépare une archive .psdi pour envoi via TNC Packet (fragments APRS).

L'archive est encodée en base64 puis découpée en fragments compatibles
avec le format APRS User-Defined de TCQ:
    {QP:ID:NN/TT:DATA}

Args:
    archive_data: Bytes de l'archive .psdi
    max_payload:  Taille max du payload par fragment (défaut 170)

Returns:
    Tuple (msg_id, fragments, info)
    - msg_id    : Identifiant unique du message (4 hex)
    - fragments : Liste de strings prêts pour send_ui_frame()
    - info      : dict avec nombre de fragments, taille totale, etc.
"""
            b64_data = base64.b64encode(archive_data).decode('ascii')
            msg_id = hashlib.md5(f'''{time.time()}{len(archive_data)}'''.encode()).hexdigest()[:4].upper()
            fragments = []
            offset = 0
            frag_num = 0
            if offset < len(b64_data):
                chunk = b64_data[offset:offset + max_payload]
                fragments.append(chunk)
                offset += max_payload
                frag_num += 1
                if offset < len(b64_data):
                    continue
            total_frags = len(fragments)
            aprs_messages = []
            for i, chunk in enumerate(fragments):
                msg = f'''{{Q{TNC_FRAME_TYPE_CODE}:{msg_id}:{i + 1:02d}/{total_frags:02d}:{chunk}'''
                aprs_messages.append(msg)
            info = {
                'msg_id': max_payload,
                'total_fragments': None,
                'archive_size': sum,
                'b64_size': (lambda .0: for m in .0:
len(m).0)(aprs_messages()),
                'max_payload': None,
                'total_air_bytes': _estimate_times,
                'estimates': sum((lambda .0: for m in .0:
len(m).0)(aprs_messages())) }
            logger.info(f'''[pdf_trans] TNC prepare: {len(archive_data):,} octets → {total_frags} fragments (ID={msg_id})''')
            return (msg_id, aprs_messages, info)

        
        def reassemble_from_tnc(fragments_dict, total_expected):
            """
Réassemble une archive .psdi à partir des fragments TNC reçus.

Args:
    fragments_dict: Dict {numéro_fragment: data_string} (1-indexed)
    total_expected: Nombre total de fragments attendus

Returns:
    Tuple (archive_bytes, info)
    - archive_bytes : bytes de l'archive (None si incomplet)
    - info          : dict avec statut, fragments reçus/manquants
"""
            received = len(fragments_dict)
            for None in missing,:
                if not i not in fragments_dict:
                    continue
            missing, = range(1, total_expected + 1), []
            info = {
                'received': received,
                'total': total_expected,
                'missing': missing,
                'complete': received == total_expected }
            if not info['complete']:
                logger.warning(f'''[pdf_trans] TNC reassemble: incomplet {received}/{total_expected}, manquants: {missing}''')
            return (None, info)
            b64_data = ''
            for i in range(1, total_expected + 1):
                b64_data += fragments_dict[i]
            
            try:
                archive_bytes = base64.b64decode(b64_data)
                if len(archive_bytes) < 4 or archive_bytes[:4] != ARCHIVE_MAGIC:
                    info['error'] = 'Signature PSDI invalide après réassemblage'
                    logger.error(f'''[pdf_trans] {info['error']}''')
                return (None, info)
                info['archive_size'] = len(archive_bytes)
                logger.info(f'''[pdf_trans] TNC reassemble OK: {len(archive_bytes):,} octets''')
                return (archive_bytes, info)
                
            except Exception:
                None = None
                info['error'] = f'''Erreur décodage base64: {e}'''
                logger.error(f'''[pdf_trans] TNC reassemble: {info['error']}''')

            del e
            return None
            None = None
            del e

        
        def parse_tnc_fragment(message):
            '''
Parse un fragment TNC PSDI reçu au format APRS User-Defined.

Format attendu: {QP:ID:NN/TT:DATA

Args:
    message: Message APRS reçu

Returns:
    dict avec {msg_id, frag_num, total_frags, data} ou None si pas un PSDI
'''
            if not message.startswith(f'''{{Q{TNC_FRAME_TYPE_CODE}:'''):
                pass
            return None
            
            try:
                content = message[1:]
                parts = content.split(':', 3)
                if len(parts) < 4:
                    pass
                return None
                
                try:
                    type_code = parts[0]
                    msg_id = parts[1]
                    frag_info = parts[2]
                    data = parts[3]
                    if '/' not in frag_info:
                        pass
                    return None
                    
                    try:
                        (frag_num, total_frags) = map(int, frag_info.split('/'))
                        return {
                            'msg_id': msg_id,
                            'frag_num': frag_num,
                            'total_frags': total_frags,
                            'data': data }

                    return None



        
        def prepare_for_vara(archive_data):
            """
Prépare une archive .psdi pour envoi via VARA (trame robuste).

Utilise le format de trame VARA de TCQ:
    <VARA:VPDF:SIZE:CRC32>BASE64_DATA</VARA>

Args:
    archive_data: Bytes de l'archive .psdi

Returns:
    Tuple (vara_frame, info)
    - vara_frame : bytes ASCII de la trame VARA complète
    - info       : dict avec taille, CRC, etc.
"""
            crc = zlib.crc32(archive_data) & 0xFFFFFFFF
            crc_hex = f'''{crc:08X}'''
            b64_data = base64.b64encode(archive_data).decode('ascii')
            header = f'''<VARA:{VARA_FRAME_TYPE}:{len(archive_data)}:{crc_hex}>'''
            frame = f'''{header}{b64_data}</VARA>'''
            frame_bytes = frame.encode('ascii')
            info = {
                'archive_size': len(archive_data),
                'frame_size': len(frame_bytes),
                'crc32': crc_hex,
                'b64_size': len(b64_data),
                'estimates': _estimate_times(len(frame_bytes)) }
            logger.info(f'''[pdf_trans] VARA prepare: {len(archive_data):,} octets → trame {len(frame_bytes):,} octets (CRC={crc_hex})''')
            return (frame_bytes, info)

        
        def decode_from_vara(vara_data):
            """
Décode une trame VARA PSDI reçue et extrait l'archive.

Args:
    vara_data: Données brutes reçues du canal VARA

Returns:
    Tuple (archive_bytes, info)
    - archive_bytes : bytes de l'archive (None si erreur)
    - info          : dict avec statut, CRC, etc.
"""
            info = {
                'success': False,
                'crc_valid': False,
                'error': None }
            
            try:
                text = vara_data.decode('ascii', errors = 'ignore') if isinstance(vara_data, bytes) else vara_data
                start_marker = f'''<VARA:{VARA_FRAME_TYPE}:'''
                end_marker = '</VARA>'
                start_pos = text.find(start_marker)
                if start_pos == -1:
                    start_marker = '<VARA:'
                    start_pos = text.find(start_marker)
                    if start_pos == -1:
                        info['error'] = 'Marqueur <VARA:VPDF: non trouvé'
                return (None, info)
                
                try:
                    end_pos = text.find(end_marker, start_pos)
                    if end_pos == -1:
                        info['error'] = 'Marqueur </VARA> non trouvé'
                    return (None, info)
                    
                    try:
                        frame = text[start_pos:end_pos + len(end_marker)]
                        header_end = frame.find('>')
                        header = frame[len('<VARA:'):header_end]
                        b64_data = frame[header_end + 1:-len(end_marker)]
                        parts = header.split(':')
                        if len(parts) < 3:
                            info['error'] = f'''Header incomplet: {header}'''
                        return (None, info)
                        
                        try:
                            frame_type = parts[0]
                            expected_size = int(parts[1])
                            expected_crc = parts[2]
                            info['frame_type'] = frame_type
                            info['expected_size'] = expected_size
                            info['expected_crc'] = expected_crc
                            archive_bytes = base64.b64decode(b64_data)
                            info['actual_size'] = len(archive_bytes)
                            actual_crc = zlib.crc32(archive_bytes) & 0xFFFFFFFF
                            actual_crc_hex = f'''{actual_crc:08X}'''
                            info['actual_crc'] = actual_crc_hex
                            info['crc_valid'] = actual_crc_hex == expected_crc
                            if not info['crc_valid']:
                                pass
                            logger.warning(f'''[pdf_trans] VARA CRC invalide: reçu={actual_crc_hex}, attendu={expected_crc}''')
                            if len(archive_bytes) >= 4 and archive_bytes[:4] == ARCHIVE_MAGIC:
                                info['success'] = True
                            else:
                                info['error'] = 'Signature PSDI invalide'
                                return (None, info)
                            None.info(f'''[pdf_trans] VARA decode OK: {len(archive_bytes):,} octets, CRC={'OK' if info['crc_valid'] else 'FAIL'}''')
                            return (archive_bytes, info)
                        except Exception:
                            e = None
                            info['error'] = str(e)
                            logger.error(f'''[pdf_trans] VARA decode erreur: {e}''')

                        del e
                        return None
                        None = None
                        del e




        
        def get_transfer_info(data_size):
            '''
Retourne les estimations de temps de transmission pour une taille donnée.

Args:
    data_size: Taille en octets

Returns:
    dict avec estimations pour chaque mode radio
'''
            return {
                'size_bytes': data_size,
                'size_kb': round(data_size / 1024, 1),
                'estimates': _estimate_times(data_size),
                'tnc_fragments': (data_size * 4 // 3 + TNC_MAX_PAYLOAD - 1) // TNC_MAX_PAYLOAD }

        
        def get_quality_presets():
            '''Retourne les préréglages de qualité disponibles.'''
            for k, v in :
                pass
            
            return QUALITY_PRESETS.items(), { }
            

        
        def validate_archive(archive_data):
            """
Valide une archive .psdi sans la décompresser entièrement.

Args:
    archive_data: Bytes de l'archive

Returns:
    dict avec: valid, version, checksum_ok, error
"""
            result = {
                'valid': False,
                'version': None,
                'checksum_ok': False,
                'error': None }
            if len(archive_data) < 6:
                result['error'] = 'Archive trop courte'
            return result
            if archive_data[:4] != ARCHIVE_MAGIC:
                result['error'] = 'Signature PSDI manquante'
            return result
            version = struct.unpack_from('<H', archive_data, 4)[0]
            result['version'] = version
            if version == ARCHIVE_VERSION_STRUCT:
                
                try:
                    pos = 6
                    checksum = struct.unpack_from('<I', archive_data, pos)[0]
                    pos += 4
                    manifest_size = struct.unpack_from('<I', archive_data, pos)[0]
                    pos += 4
                    manifest_compressed = archive_data[pos:pos + manifest_size]
                    manifest_bytes = lzma.decompress(manifest_compressed, format = lzma.FORMAT_RAW, filters = [
                        {
                            'id': lzma.FILTER_LZMA2 }])
                    actual_crc = zlib.crc32(manifest_bytes) & 0xFFFFFFFF
                    result['checksum_ok'] = actual_crc == checksum
                    result['valid'] = True
                    return result
                    if version == ARCHIVE_VERSION_IMAGE:
                        
                        try:
                            nb_pages = struct.unpack_from('<H', archive_data, 6)[0]
                            result['nb_pages'] = nb_pages
                            if nb_pages > 0:
                                nb_pages > 0
                            result['valid'] = nb_pages < 100
                            result['checksum_ok'] = True
                            return result
                            result['error'] = f'''Version non supportée: {version}'''
                            return result
                            except Exception:
                                e = None
                                result['error'] = f'''Erreur validation struct: {e}'''
                            e = None
                            del e
                            return result
                            e = None
                            del e
                        except Exception:
                            e = None
                            result['error'] = f'''Erreur validation image: {e}'''

                        e = None
                        del e
                        return result
                    e = None
                    del e


        
        def is_psdi_fragment(message):
            '''Teste si un message APRS est un fragment PSDI.'''
            return message.startswith(f'''{{Q{TNC_FRAME_TYPE_CODE}:''')

        
        def is_psdi_vara_frame(data):
            '''Teste si des données VARA contiennent une trame PSDI.'''
            text = data.decode('ascii', errors = 'ignore') if isinstance(data, bytes) else data
            return f'''<VARA:{VARA_FRAME_TYPE}:''' in text

        
        class PDFTransferManager:
            __firstlineno__ = 731
            __doc__ = '\nGestionnaire de transferts PDF pour intégration dans TCQ.\n\nGère le cycle complet : compression, fragmentation, envoi progressif,\nréception, réassemblage, et recomposition du PDF.\n\nUsage dans TCQ:\n    self.pdf_transfer = PDFTransferManager(logger_func=self._log)\n    \n    # Envoi TNC\n    self.pdf_transfer.send_pdf_tnc(\n        pdf_path, dest, quality, \n        send_func=self.tnc_client.send_ui_frame,\n        progress_func=lambda p: self.tnc_progress_var.set(p)\n    )\n    \n    # Réception TNC (dans _tnc_handle_fragmented_message)\n    if pdf_trans.is_psdi_fragment(message):\n        result = self.pdf_transfer.receive_tnc_fragment(source, message)\n        if result and result["complete"]:\n            pdf_bytes = result["pdf_bytes"]\n            ...\n    \n    # Envoi VARA\n    self.pdf_transfer.send_pdf_vara(\n        pdf_path, quality,\n        send_func=self.vara_client.send_data,\n        progress_func=...\n    )\n'
            
            def __init__(self, received_files_path = '.', logger_func = None):
                '''
Args:
    received_files_path: Répertoire de sauvegarde des fichiers reçus
    logger_func: Fonction de log TCQ: func(message, level)
'''
                self.received_files_path = received_files_path
                self._log_func = logger_func
                self.tnc_rx_buffers = { }
                self.stats = {
                    'pdf_sent': 0,
                    'pdf_received': 0,
                    'bytes_sent': 0,
                    'bytes_received': 0,
                    'errors': 0 }

            
            def _log(self, message, level = 'info'):
                '''Log via la fonction TCQ ou le logger standard.'''
                if self._log_func:
                    self._log_func(message, level)
                return None
                getattr(logger, level, logger.info)(message)

            
            def send_pdf_tnc(self, pdf_path, dest, quality = QUALITY_LOW, dest_ssid = 0, digipeaters = None, send_frame_func = None, progress_func = None, done_callback = None, skip_images = False):
                '''
Compresse et envoie un PDF via TNC Packet (synchrone).

Args:
    pdf_path:        Chemin du PDF
    dest:            Indicatif destination (ou "CQ")
    quality:         Préréglage qualité
    dest_ssid:       SSID destination
    digipeaters:     Liste [(call, ssid), ...]
    send_frame_func: Fonction d\'envoi TCQ
    progress_func:   Fonction progression: func(str)
    done_callback:   Callback de fin: func(success, info)
    skip_images:     Si True, n\'inclut pas les images (mode Fast)
'''
                info = {
                    'success': False,
                    'fragments_sent': 0,
                    'fragments_total': 0 }
                
                try:
                    if progress_func:
                        pass
                    progress_func('Compression PDF...' + ' [SANS IMAGE]' if skip_images else '')
                    (archive_bytes, compress_info) = pdf_to_archive(pdf_path, quality = quality, mode = 'struct', skip_images = skip_images)
                    self._log(f'''[PDF TX] {compress_info['source']}: {compress_info['original_size']:,}→{compress_info['archive_size']:,} octets ({compress_info['ratio_percent']}%)''', 'info')
                    (msg_id, fragments, frag_info) = prepare_for_tnc(archive_bytes)
                    info['msg_id'] = msg_id
                    info['fragments_total'] = frag_info['total_fragments']
                    info['archive_size'] = len(archive_bytes)
                    info.update(compress_info)
                    self._log(f'''[PDF TX] {frag_info['total_fragments']} fragments (ID={msg_id}) vers {dest}''', 'info')
                    if not send_frame_func:
                        info['error'] = "Pas de fonction d'envoi"
                    return info
                    
                    try:
                        success_count = 0
                        total = len(fragments)
                        for i, fragment in enumerate(fragments):
                            remaining = total - (i + 1)
                            if progress_func:
                                pass
                            progress_func(f'''PDF {msg_id}: {i + 1}/{total} — {remaining} restant{'s' if remaining > 1 else ''}''')
                            if not i < total - 1:
                                continue
                            
                            try:
                                time.sleep(TNC_INTER_FRAME_DELAY)
                                continue
                                info['fragments_sent'] = success_count
                                info['success'] = success_count == total
                                if info['success']:
                                    self._log(f'''[PDF TX] ✅ PDF envoyé: {total} trames, {len(archive_bytes):,} octets (ID={msg_id})''', 'success')
                                else:
                                    
                                    try:
                                        self._log(f'''[PDF TX] ⚠️ Envoi partiel: {success_count}/{total} trames''', 'warning')
                                        if done_callback:
                                            pass
                                        done_callback(info['success'], info)
                                        if progress_func:
                                            pass
                                        progress_func('')
                                        return info
                                    except Exception:
                                        None = None
                                        info['error'] = str(e)
                                        self._log(f'''[PDF TX] Erreur: {e}''', 'error')

                                    None = None
                                    del e
                                e = None
                                del e




            
            def receive_tnc_fragment(self, source, message):
                """
Traite un fragment TNC PSDI reçu. Réassemble automatiquement.

Args:
    source:  Indicatif de l'expéditeur
    message: Message APRS brut

Returns:
    dict si le PDF est complet:
      {complete: True, pdf_bytes, archive_bytes, info, save_path}
    dict si fragment reçu (incomplet):
      {complete: False, received, total, msg_id, missing}
    None si le message n'est pas un fragment PSDI
"""
                parsed = parse_tnc_fragment(message)
                if not parsed:
                    pass
                return None
                msg_id = parsed['msg_id']
                frag_num = parsed['frag_num']
                total_frags = parsed['total_frags']
                data = parsed['data']
                buffer_key = f'''{source}_{msg_id}'''
                if buffer_key not in self.tnc_rx_buffers:
                    self.tnc_rx_buffers[buffer_key] = {
                        'source': source,
                        'msg_id': msg_id,
                        'total': total_frags,
                        'fragments': { },
                        'timestamp': datetime.now(),
                        'last_frag_time': datetime.now() }
                self.tnc_rx_buffers[buffer_key]['fragments'][frag_num] = data
                self.tnc_rx_buffers[buffer_key]['last_frag_time'] = datetime.now()
                received = len(self.tnc_rx_buffers[buffer_key]['fragments'])
                self._log(f'''[PDF RX] {msg_id} de {source}: trame {frag_num}/{total_frags} ({received} reçues)''', 'info')
                if received >= total_frags:
                    pass
                return self._complete_tnc_reassembly(buffer_key)
                if frag_num == total_frags and received < total_frags:
                    for None in :
                        if not i not in self.tnc_rx_buffers[buffer_key]['fragments']:
                            continue
                    range(1, total_frags + 1), [], (i,) = 
                    self._log(f'''[PDF RX] {msg_id}: dernière trame reçue mais {len(missing)} trames manquantes: {missing[:10]}{'...' if len(missing) > 10 else ''}''', 'warning')
                return {
                    'complete': False,
                    'received': received,
                    'total': total_frags,
                    'msg_id': msg_id,
                    'source': source,
                    'last_frame_received': True,
                    'missing': missing }
                return {
                    'complete': False,
                    'received': received,
                    'total': total_frags,
                    'msg_id': msg_id,
                    'source': source }
                

            
            def force_complete_transfer(self, source, msg_id):
                """
Force la recomposition du PDF même si des trames manquent.
Appelé après timeout ou réception de la dernière trame.

Args:
    source: Indicatif de l'expéditeur
    msg_id: Identifiant du message
    
Returns:
    dict avec résultat ou None si buffer introuvable
"""
                buffer_key = f'''{source}_{msg_id}'''
                if buffer_key not in self.tnc_rx_buffers:
                    pass
                return None
                return self._complete_tnc_reassembly(buffer_key)

            
            def _complete_tnc_reassembly(self, buffer_key):
                '''Effectue le réassemblage et la recomposition PDF.'''
                buf = self.tnc_rx_buffers[buffer_key]
                source = buf['source']
                msg_id = buf['msg_id']
                total_frags = buf['total']
                received = len(buf['fragments'])
                self._log(f'''[PDF RX] {msg_id} réassemblage: {received}/{total_frags} trames...''', 'info')
                (archive_bytes, reasm_info) = reassemble_from_tnc(buf['fragments'], total_frags)
                del self.tnc_rx_buffers[buffer_key]
                if not archive_bytes is not None:
                    error_msg = reasm_info.get('error', 'réassemblage échoué')
                    missing = reasm_info.get('missing', [])
                    if missing:
                        pass
                    error_msg += f''' (trames manquantes: {missing[:10]}{'...' if len(missing) > 10 else ''})'''
                    self._log(f'''[PDF RX] Erreur: {error_msg}''', 'error')
                return {
                    'complete': True,
                    'success': False,
                    'error': error_msg,
                    'msg_id': msg_id,
                    'source': source }
                
                try:
                    (pdf_bytes, rebuild_info) = archive_to_pdf(archive_bytes)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    save_filename = f'''pdf_packet_{source}_{timestamp}.pdf'''
                    save_path = os.path.join(self.received_files_path, save_filename)
                    
                    try:
                        f = open(save_path, 'wb')
                        f.write(pdf_bytes)
                        
                        try:
                            None(None, None)
                            self._log(f'''[PDF RX] ✅ PDF reçu de {source}: {save_filename} ({len(pdf_bytes):,} octets)''', 'success')
                            return {
                                'complete': True,
                                'success': True,
                                'pdf_bytes': pdf_bytes,
                                'archive_bytes': archive_bytes,
                                'save_path': save_path,
                                'source': source,
                                'msg_id': msg_id,
                                'info': rebuild_info }
                            except Exception:
                                None = None
                                self._log(f'''[PDF RX] Erreur recomposition PDF: {e}''', 'error')
                            del e
                            return None
                            None = None
                            del e
                            with None:
                                if not None:
                                    pass
                            
                            try:
                                continue
                                
                                try:
                                    pass
                                except Exception:
                                    e = None
                                    self._log(f'''[PDF RX] Erreur sauvegarde: {e}''', 'warning')
                                    save_path = None

                                e = None
                                del e
                                continue
                                e = None
                                del e





            
            def send_pdf_vara(self, pdf_path, quality = QUALITY_MEDIUM, send_data_func = None, progress_func = None, done_callback = None, skip_images = False):
                """
Compresse et envoie un PDF via VARA (trame unique robuste).

Destiné à être appelé depuis un thread séparé dans TCQ.

Args:
    pdf_path:       Chemin du PDF
    quality:        Préréglage qualité
    send_data_func: VaraClient.send_data(bytes) → bool
    progress_func:  Fonction progression: func(str)
    done_callback:  Callback de fin: func(success, info)
    skip_images:    Si True, n'inclut pas les images (mode Fast)

Returns:
    dict avec résultats de l'envoi
"""
                info = {
                    'success': False }
                
                try:
                    if progress_func:
                        pass
                    progress_func('Compression PDF...' + ' [SANS IMAGE]' if skip_images else '')
                    (archive_bytes, compress_info) = pdf_to_archive(pdf_path, quality = quality, mode = 'struct', skip_images = skip_images)
                    info.update(compress_info)
                    arch_kb = compress_info['archive_size'] // 1024
                    orig_kb = compress_info['original_size'] // 1024
                    ratio = compress_info.get('ratio_percent', 0)
                    self._log(f'''[PDF VARA TX] {compress_info['source']}: {compress_info['original_size']:,}→{compress_info['archive_size']:,} octets''', 'info')
                    if progress_func:
                        pass
                    progress_func(f'''Compressé: {orig_kb} Ko → {arch_kb} Ko ({ratio:.0f}%)''')
                    (vara_frame, vara_info) = prepare_for_vara(archive_bytes)
                    info.update(vara_info)
                    if not send_data_func:
                        info['error'] = "Pas de fonction d'envoi VARA"
                    return info
                    
                    try:
                        total_kb = len(vara_frame) // 1024
                        if progress_func:
                            pass
                        progress_func(f'''Envoi VARA: {total_kb} Ko''')
                        if send_data_func(vara_frame):
                            info['success'] = True
                            if progress_func:
                                pass
                            progress_func(f'''Transmission radio terminée ({total_kb} Ko)''')
                            self._log(f'''[PDF VARA TX] ✅ PDF envoyé: {len(vara_frame):,} octets''', 'success')
                        else:
                            
                            try:
                                'Échec envoi VARA' = None
                                self._log('[PDF VARA TX] ⚠️ Échec envoi', 'warning')
                                if done_callback:
                                    pass
                                done_callback(info['success'], info)
                                if progress_func:
                                    pass
                                progress_func('')
                                return info
                            except Exception:
                                None = None
                                info['error'] = str(e)
                                self._log(f'''[PDF VARA TX] Erreur: {e}''', 'error')

                            None = None
                            del e
                        e = None
                        del e



            
            def receive_vara_pdf(self, vara_data):
                '''
Traite une trame VARA PSDI reçue et recompose le PDF.

Args:
    vara_data: Données brutes reçues

Returns:
    dict avec {success, pdf_bytes, save_path, info} ou None
'''
                if not is_psdi_vara_frame(vara_data):
                    pass
                return None
                (archive_bytes, decode_info) = decode_from_vara(vara_data)
                if not archive_bytes is not None:
                    self._log(f'''[PDF VARA RX] Erreur décodage: {decode_info.get('error', '?')}''', 'error')
                return {
                    'success': False,
                    'error': decode_info.get('error') }
                
                try:
                    (pdf_bytes, rebuild_info) = archive_to_pdf(archive_bytes)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    save_filename = f'''pdf_vara_{timestamp}.pdf'''
                    save_path = os.path.join(self.received_files_path, save_filename)
                    
                    try:
                        f = open(save_path, 'wb')
                        f.write(pdf_bytes)
                        
                        try:
                            None(None, None)
                            self._log(f'''[PDF VARA RX] ✅ PDF reçu: {save_filename} ({len(pdf_bytes):,} octets) CRC={'OK' if decode_info['crc_valid'] else 'FAIL'}''', 'success')
                            return {
                                'success': True,
                                'pdf_bytes': pdf_bytes,
                                'archive_bytes': archive_bytes,
                                'save_path': save_path,
                                'crc_valid': decode_info['crc_valid'],
                                'info': rebuild_info }
                            except Exception:
                                None = None
                                self._log(f'''[PDF VARA RX] Erreur recomposition: {e}''', 'error')
                            del e
                            return None
                            None = None
                            del e
                            with None:
                                if not None:
                                    pass
                            
                            try:
                                continue
                                
                                try:
                                    pass
                                except Exception:
                                    e = None
                                    self._log(f'''[PDF VARA RX] Erreur sauvegarde: {e}''', 'warning')
                                    save_path = None

                                e = None
                                del e
                                continue
                                e = None
                                del e





            
            def cleanup_stale_buffers(self, max_age_seconds = 300):
                '''Nettoie les buffers TNC incomplets trop anciens.'''
                now = datetime.now()
                stale_keys = []
                for key, buf in self.tnc_rx_buffers.items():
                    age = (now - buf['timestamp']).total_seconds()
                    if not age > max_age_seconds:
                        continue
                    stale_keys.append(key)
                for key in stale_keys:
                    buf = self.tnc_rx_buffers.pop(key)
                    received = len(buf['fragments'])
                    self._log(f'''[PDF RX] Buffer expiré: {buf['msg_id']} de {buf['source']} ({received}/{buf['total']} fragments)''', 'warning')

            
            def get_stats(self):
                '''Retourne les statistiques de transfert.'''
                return dict(self.stats)

            __static_attributes__ = ('_log_func', 'received_files_path', 'stats', 'tnc_rx_buffers')

        
        def _detect_optimal_mode(pdf_path):
            '''Détecte si un PDF doit être traité en mode image plutôt que struct.

Renvoie (mode, raison) :
  - ("image", "rotation") si au moins une page a un /Rotate != 0 ;
  - ("image", "scan")     si le document est essentiellement scanné
                          (texte extractible négligeable + grande
                          couverture image sur la majorité des pages) ;
  - ("struct", None)      sinon (vrai PDF texte natif, non tourné).

En cas d\'erreur d\'analyse, on reste prudemment en mode struct.
'''
            TEXT_MIN_CHARS = 50
            IMG_COVER_MIN = 0.5
            
            try:
                doc = fitz.open(pdf_path)
                
                try:
                    nb_pages = len(doc)
                    if nb_pages == 0:
                        doc.close()
                    return ('struct', None)
                    
                    try:
                        image_only_pages = 0
                        for page in doc:
                            if page.rotation % 360 != 0:
                                doc
                                doc.close()
                            return ('image', 'rotation')
                            
                            try:
                                text_len = len(page.get_text('text').strip())
                                if not abs(page.rect.width * page.rect.height):
                                    abs(page.rect.width * page.rect.height)
                                page_area = 1
                                img_area = 0
                                for blk in page.get_text('dict').get('blocks', []):
                                    if not blk.get('type') == 1:
                                        continue
                                    b = blk['bbox']
                                    img_area += abs((b[2] - b[0]) * (b[3] - b[1]))
                                
                                try:
                                    big_image = img_area / page_area >= IMG_COVER_MIN
                                    if not text_len < TEXT_MIN_CHARS:
                                        continue
                                    
                                    try:
                                        if not big_image:
                                            continue
                                        
                                        try:
                                            image_only_pages += 1
                                            continue
                                            if image_only_pages >= max(1, nb_pages // 2):
                                                doc.close()
                                            return ('image', 'scan')
                                            doc.close()
                                            return ('struct', None)
                                            return ('struct', None)
                                            except Exception:
                                                
                                                try:
                                                    continue
                                                    
                                                    try:
                                                        pass
                                                    except Exception:
                                                        
                                                        try:
                                                            doc.close()
                                                            return ('struct', None)
                                                            
                                                            try:
                                                                pass
                                                            except:
                                                                doc.close()












        
        def _compress_image_mode(pdf_path, params, progress_cb = None):
            '''Compresse le PDF en rendant chaque page en image JPEG.'''
            doc = fitz.open(pdf_path)
            dpi = params['dpi']
            quality = params['jpeg_quality']
            archive = io.BytesIO()
            archive.write(ARCHIVE_MAGIC)
            archive.write(struct.pack('<H', ARCHIVE_VERSION_IMAGE))
            archive.write(struct.pack('<H', len(doc)))
            for i, page in enumerate(doc):
                _progress(progress_cb, i + 1, len(doc) + 1, f'''Rendu page {i + 1}/{len(doc)} ({dpi} DPI)...''')
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix = mat)
                img = Image.frombytes('RGB', [
                    pix.width,
                    pix.height], pix.samples)
                buf = io.BytesIO()
                img.save(buf, format = 'JPEG', quality = quality, optimize = True)
                img_bytes = buf.getvalue()
                compressed = lzma.compress(img_bytes, format = lzma.FORMAT_RAW, filters = [
                    {
                        'id': lzma.FILTER_LZMA2,
                        'preset': params['lzma_preset'] }])
                archive.write(struct.pack('<f', page.rect.width))
                archive.write(struct.pack('<f', page.rect.height))
                archive.write(struct.pack('<B', flags))
                archive.write(struct.pack('<I', len(use_data)))
                archive.write(use_data)
            enumerate(doc)
            doc.close()
            return archive.getvalue()

        
        def _rebuild_image_mode(archive_data, progress_cb = None):
            '''Recompose un PDF depuis une archive mode image.'''
            pos = 6
            nb_pages = struct.unpack_from('<H', archive_data, pos)[0]
            pos += 2
            doc = fitz.open()
            for i in range(nb_pages):
                _progress(progress_cb, i + 1, nb_pages + 1, f'''Recomposition page {i + 1}/{nb_pages}...''')
                pw = struct.unpack_from('<f', archive_data, pos)[0]
                pos += 4
                ph = struct.unpack_from('<f', archive_data, pos)[0]
                pos += 4
                flags = struct.unpack_from('<B', archive_data, pos)[0]
                pos += 1
                img_size = struct.unpack_from('<I', archive_data, pos)[0]
                pos += 4
                img_data = archive_data[pos:pos + img_size]
                pos += img_size
                if flags & 1:
                    pass
                img_data = lzma.decompress(img_data, format = lzma.FORMAT_RAW, filters = [
                    {
                        'id': lzma.FILTER_LZMA2 }])
                page = doc.new_page(width = pw, height = ph)
                page.insert_image(fitz.Rect(0, 0, pw, ph), stream = img_data)
            pdf_buf = io.BytesIO()
            doc.save(pdf_buf, deflate = True, garbage = 4)
            doc.close()
            info = {
                'mode': 'image',
                'pages': nb_pages }
            return (pdf_buf.getvalue(), info)

        
        def _compress_struct_mode(pdf_path, params, progress_cb = None, skip_images = False):
            '''Compresse le PDF en extrayant texte et images séparément.'''
            doc = fitz.open(pdf_path)
            _progress(progress_cb, 1, 3, 'Extraction des éléments...')
            images_data = { }
            if not skip_images:
                unique_xrefs = { }
                for page in doc:
                    for img_info in page.get_images(full = True):
                        xref = img_info[0]
                        if not xref not in unique_xrefs:
                            continue
                        unique_xrefs[xref] = img_info
                for xref in unique_xrefs:
                    img = doc.extract_image(xref)
                    img_bytes = img['image']
                    h = img['height']
                    w = img['width']
                    optimized = _optimize_image(img_bytes, w, h, params)
                    if not optimized:
                        continue
                    images_data[str(xref)] = optimized
            manifest = {
                'v': PDF_TRANS_VERSION,
                'src': os.path.basename(pdf_path),
                'pages': [],
                'page_sizes': [],
                'images': { } }
            _progress(progress_cb, 2, 3, 'Construction du manifeste...')
            for opt_bytes, orig_w, orig_h in images_data.items():
                manifest['images'][xref_str] = {
                    'w': orig_w,
                    'h': orig_h,
                    's': len(opt_bytes) }
            images_data.items()
            for page_idx, page in enumerate(doc):
                page_data = _extract_page_struct(page, page_idx, images_data)
                manifest['pages'].append(page_data)
                manifest['page_sizes'].append({
                    'w': page.rect.width,
                    'h': page.rect.height })
            doc.close()
            manifest_json = json.dumps(manifest, ensure_ascii = False, separators = (',', ':'))
            manifest_bytes = manifest_json.encode('utf-8')
            manifest_compressed = lzma.compress(manifest_bytes, format = lzma.FORMAT_RAW, filters = [
                {
                    'id': lzma.FILTER_LZMA2,
                    'preset': params['lzma_preset'] }])
            archive = io.BytesIO()
            archive.write(ARCHIVE_MAGIC)
            archive.write(struct.pack('<H', ARCHIVE_VERSION_STRUCT))
            checksum = zlib.crc32(manifest_bytes) & 0xFFFFFFFF
            archive.write(struct.pack('<I', checksum))
            archive.write(struct.pack('<I', len(manifest_compressed)))
            archive.write(manifest_compressed)
            archive.write(struct.pack('<H', len(images_data)))
            for xref_str in sorted(images_data.keys(), key = (lambda x: int(x))):
                (opt_bytes,)
                lzma.compress(opt_bytes, format = lzma.FORMAT_RAW, filters = [
                    {
                        'id': lzma.FILTER_LZMA2,
                        'preset': 6 }]) = images_data[xref_str]
                archive.write(struct.pack('<H', int(xref_str)))
                archive.write(struct.pack('<B', flags))
                archive.write(struct.pack('<I', len(use_data)))
                archive.write(use_data)
            return archive.getvalue()

        
        def _rebuild_struct_mode(archive_data, progress_cb = None):
            '''Recompose un PDF depuis une archive mode structuré.'''
            pos = 6
            checksum = struct.unpack_from('<I', archive_data, pos)[0]
            pos += 4
            manifest_size = struct.unpack_from('<I', archive_data, pos)[0]
            pos += 4
            manifest_compressed = archive_data[pos:pos + manifest_size]
            pos += manifest_size
            manifest_bytes = lzma.decompress(manifest_compressed, format = lzma.FORMAT_RAW, filters = [
                {
                    'id': lzma.FILTER_LZMA2 }])
            actual_crc = zlib.crc32(manifest_bytes) & 0xFFFFFFFF
            crc_ok = actual_crc == checksum
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            nb_images = struct.unpack_from('<H', archive_data, pos)[0]
            pos += 2
            images = { }
            for _ in range(nb_images):
                xref_id = struct.unpack_from('<H', archive_data, pos)[0]
                pos += 2
                flags = struct.unpack_from('<B', archive_data, pos)[0]
                pos += 1
                img_size = struct.unpack_from('<I', archive_data, pos)[0]
                pos += 4
                img_data = archive_data[pos:pos + img_size]
                pos += img_size
                if flags & 1:
                    pass
                img_data = lzma.decompress(img_data, format = lzma.FORMAT_RAW, filters = [
                    {
                        'id': lzma.FILTER_LZMA2 }])
                images[str(xref_id)] = img_data
            _progress(progress_cb, 1, 2, 'Recomposition des pages...')
            FONT_MAP = {
                'R': 'helv',
                'B': 'hebo',
                'I': 'heit',
                'BI': 'hebi' }
            doc = fitz.open()
            for page_data in manifest['pages']:
                page_idx = page_data['pn'] - 1
                ps = manifest['page_sizes'][page_idx]
                page = doc.new_page(width = ps['w'], height = ps['h'])
                shape = page.new_shape()
                for d in page_data.get('dr', []):
                    if not d['t'] == 'rect':
                        continue
                    rect = fitz.Rect(d['r'])
                    fill_color = _hex_to_rgb(d.get('f', ''))
                    stroke_color = _hex_to_rgb(d.get('c', ''))
                    line_width = d.get('w', 0)
                    shape.draw_rect(rect)
                    shape.finish(color = stroke_color, fill = fill_color, width = line_width if line_width > 0.1 else 0)
                page_data.get('dr', [])
                shape.commit()
                for ref in page_data.get('ir', []):
                    xref_str = ref['x']
                    if not xref_str in images:
                        continue
                    page.insert_image(fitz.Rect(ref['b']), stream = images[xref_str])
                page_data.get('ir', [])
                FAMILY_MAP = {
                    's': 'Helvetica, Arial, sans-serif',
                    't': "'Times New Roman', Times, serif",
                    'm': "'Courier New', Courier, monospace" }
                for block in page_data.get('tb', []):
                    for line in block.get('l', []):
                        spans = line.get('s', [])
                        if not spans:
                            continue
                        line_bbox = line['b']
                        html_parts = []
                        for span in spans:
                            text = span.get('t', '')
                            if not text:
                                continue
                            text_esc = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                            font_key = span.get('f', 'R')
                            fontsize = span.get('sz', 8)
                            color_int = span.get('c', 0)
                            r = color_int >> 16 & 255
                            g = color_int >> 8 & 255
                            b_val = color_int & 255
                            color_hex = f'''#{r:02x}{g:02x}{b_val:02x}'''
                            fam_css = FAMILY_MAP.get(span.get('fa', 's'), FAMILY_MAP['s'])
                            style = f'''font-family:{fam_css};font-size:{fontsize}pt;color:{color_hex};line-height:1;margin:0;padding:0;'''
                            if font_key == 'B':
                                html_parts.append(f'''<b style="{style}">{text_esc}</b>''')
                                continue
                            if font_key == 'I':
                                html_parts.append(f'''<i style="{style}">{text_esc}</i>''')
                                continue
                            if font_key == 'BI':
                                html_parts.append(f'''<b><i style="{style}">{text_esc}</i></b>''')
                                continue
                            html_parts.append(f'''<span style="{style}">{text_esc}</span>''')
                        block.get('l', [])
                        if not html_parts:
                            continue
                        html = ''.join(html_parts)
                        line_height = line_bbox[3] - line_bbox[1]
                        max_font_size = (lambda .0: for span in .0:
span.get('sz', 8).0)(spans(), default = 8)
                        rect_height = max(line_height * 1.15, max_font_size * 1.2, 2)
                        rect = fitz.Rect(line_bbox[0] - 0.5, line_bbox[1], line_bbox[2] + 0.5, line_bbox[1] + rect_height)
                        base_css = '* { margin:0; padding:0; box-sizing:border-box; }html, body { margin:0; padding:0; line-height:1; }'
                        page.insert_htmlbox(rect, html, css = base_css, scale_low = 0.5)
                    max
                page_data.get('tb', [])
                checks = page_data.get('ck', [])
                if not checks:
                    continue
                ck_shape = page.new_shape()
                drawn = False
                for cb in checks:
                    (x0, y0, x1, y1) = cb
                    w_box = x1 - x0
                    h_box = y1 - y0
                    if w_box <= 0 or h_box <= 0:
                        continue
                    p1 = fitz.Point(x0 + 0.2 * w_box, y0 + 0.55 * h_box)
                    p2 = fitz.Point(x0 + 0.42 * w_box, y0 + 0.78 * h_box)
                    p3 = fitz.Point(x0 + 0.82 * w_box, y0 + 0.18 * h_box)
                    ck_shape.draw_polyline([
                        p1,
                        p2,
                        p3])
                    lw = max(0.8, min(2.2, 0.16 * min(w_box, h_box)))
                    ck_shape.finish(color = (0, 0, 0), fill = None, width = lw, closePath = False)
                    drawn = True
                checks
                if not drawn:
                    continue
                ck_shape.commit()
            manifest['pages']
            pdf_buf = io.BytesIO()
            doc.save(pdf_buf, deflate = True, deflate_images = True, garbage = 4, clean = True)
            doc.close()
            info = {
                'mode': 'struct',
                'pages': len(manifest['pages']),
                'crc_ok': crc_ok }
            return (pdf_buf.getvalue(), info)
            continue
            except Exception:
                for span in spans:
                    text = span.get('t', '')
                    if not text.strip():
                        continue
                    span_bbox = span.get('b', line_bbox)
                    page.insert_text(fitz.Point(span_bbox[0], span_bbox[3]), text, fontname = FONT_MAP.get(span.get('f', 'R'), 'helv'), fontsize = span.get('sz', 8))
                    except Exception:
                        spans
                spans
            continue
            continue
            continue

        
        def _rects_from_path_items(items):
            '''Reconstruit les rectangles axis-aligned d\'un path de dessin.

Gère deux formes de tracé rencontrées dans les PDF :

  - sous-items "re" : rectangle natif (PDF Microsoft Print To PDF, Word…) ;
  - groupes de segments "l" : rectangle tracé en 4 lignes fermées. C\'est
    le cas des grilles et fonds de cellule produits par LibreOffice (et
    par certaines versions de MuPDF, d\'où un comportement qui pouvait
    différer entre les builds Windows et Linux). Sans cette prise en
    charge, cadres et couleurs de tableau disparaissaient à la
    recomposition (mode struct).

Renvoie une liste de fitz.Rect (bounding box de chaque sous-rectangle).
'''
            rects = []
            pts = []
            start = None
            
            def _flush():
                if len(pts) >= 4:
                    for None in xs,:
                        pass
                    xs, = pts, []
                    for None in ys,:
                        pass
                    ys, = pts, []
                rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
                pts.clear()
                return None
                
                

            if not items:
                items
            for it in []:
                if not it:
                    continue
                kind = it[0]
                if kind == 're':
                    _flush()
                    start = None
                    rects.append(fitz.Rect(it[1]))
                    continue
                if kind == 'l':
                    p1 = it[2]
                    p0 = it[1]
                    if start is None and abs(p1.x - start.x) < 0.6 and abs(p1.y - start.y) < 0.6 and len(pts) >= 4:
                        _flush()
                    start = None
                    continue
                _flush()
                start = None
            _flush()
            return rects
            continue
            continue

        
        def _extract_page_struct(page, page_idx, images_data):
            '''Extrait la structure d\'une page (format compact).

Utilise get_image_info(xrefs=True) pour récupérer la position des images
car get_text("dict") ne retourne pas les images XObject de ce type de PDF.
Les dessins vectoriels (rectangles colorés) sont extraits pour la mise en page.
'''
            page_data = {
                'pn': page_idx + 1,
                'tb': [],
                'ir': [],
                'dr': [],
                'ck': [] }
            blocks = page.get_text('dict', flags = fitz.TEXT_PRESERVE_WHITESPACE)['blocks']
            for block in blocks:
                if not block['type'] == 0:
                    continue
                for None in :
                    pass
                {
                    'b': x,,
                    'l': [] } = block['bbox'], [], 
                for line in block['lines']:
                    for None in :
                        pass
                    {
                        'b': x,,
                        's': [] } = line['bbox'], [], 
                    for span in line['spans']:
                        if not span['text'].strip():
                            continue
                        font_lower = span['font'].lower()
                        if 'bold' in font_lower and 'italic' in font_lower:
                            fk = 'BI'
                        elif 'bold' in font_lower:
                            fk = 'B'
                        elif 'italic' in font_lower:
                            fk = 'I'
                        else:
                            fk = 'R'
                        if (lambda .0: for s in .0:
s in font_lower.0)(('courier', 'mono', 'consolas', 'menlo', 'cour')()):
                            fam = 'm'
                        elif (lambda .0: for s in .0:
s in font_lower.0)(('times', 'serif', 'cambria', 'georgia', 'garamond', 'tinos', 'liberation serif', 'freeserif', 'dejavu serif', 'noto serif')()):
                            fam = 't'
                        else:
                            fam = 's'
                        text_raw = span['text']
                        text_normalized = text_raw.replace('ﬀ', 'ff').replace('ﬁ', 'fi').replace('ﬂ', 'fl').replace('ﬃ', 'ffi').replace('ﬄ', 'ffl').replace('ﬅ', 'ft').replace('ﬆ', 'st').replace('Ɵ', 'ti').replace('Ʃ', 'tt')
                        if '�' in text_normalized:
                            chars = list(text_normalized)
                            for i, c in enumerate(chars):
                                if not c == '�':
                                    continue
                                nxt = chars[i + 1] if i + 1 < len(chars) else ''
                                if nxt in ('\xc3\xa9', '\xc3\xa8', '\xc3\xaa'):
                                    chars[i] = 'tt'
                                    continue
                                if nxt == 'e':
                                    prv = chars[i - 1] if i > 0 else ''
                                    if prv == 'u':
                                        prv2 = chars[i - 2] if i > 1 else ''
                                        if prv2 == 'l':
                                            chars[i] = 'tt'
                                            continue
                                        chars[i] = 'ti'
                                        continue
                                    chars[i] = 'ti'
                                    continue
                                chars[i] = 'ti'
                            any
                        text_normalized = ''.join(chars)
                        for None in :
                            pass
                        fk({
                            't': fam,
                            'f': round(span['size'], 1),
                            'fa': span['color'],
                            'sz': span['flags'],
                            'c': ,
                            'fl': span['bbox'], [], ,
                            'b': x, })
                    ln['s'].append
                    if not ln['s']:
                        continue
                    tb['l'].append(ln)
                if not tb['l']:
                    continue
                page_data['tb'].append(tb)
            page.get_image_info(xrefs = True) = None
            seen_refs = set()
            for ii in image_info_list:
                xref = ii.get('xref', 0)
                if not xref:
                    continue
                if not str(xref) in images_data:
                    continue
                bbox = ii.get('bbox', (0, 0, 0, 0))
                ref_key = f'''{xref}_{bbox[0]:.0f}_{bbox[1]:.0f}'''
                if not ref_key not in seen_refs:
                    continue
                seen_refs.add(ref_key)
                for None in :
                    pass
                ({
                    'x': bbox, [], ,
                    'b': x, })
            page_data['ir'].append
            page.rect.width * page.rect.height = None
            for path in page.get_drawings():
                fill = path.get('fill')
                color = path.get('color')
                path_width = path.get('width', 0)
                if not fill is not None and color is not None:
                    continue
                for rect in _rects_from_path_items(path.get('items', [])):
                    if not rect is not None:
                        continue
                    rect = fitz.Rect(rect).normalize()
                    if rect.is_empty or rect.is_infinite:
                        continue
                    sub_area = rect.width * rect.height
                    if sub_area > 0.8 * page_area and fill is None and isinstance(fill, (list, tuple)) and len(fill) == 3:
                        luminance = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
                        if luminance < 0.3:
                            continue
                    for None in :
                        pass
                    {
                        't': rect, [], ,
                        'r': x, } = 
                    if not fill is None:
                        dr['f'] = _color_to_hex(fill)
                    if not color is None:
                        dr['c'] = _color_to_hex(color)
                    if path_width and path_width > 0.1:
                        dr['w'] = round(path_width, 1)
                    page_data['dr'].append(dr)
                _rects_from_path_items(path.get('items', []))
            image_info_list
            
            try:
                widgets_iter = page.widgets()
                if widgets_iter:
                    
                    try:
                        for w in widgets_iter:
                            ftype = getattr(w, 'field_type', None)
                            if not getattr(w, 'field_type_string', ''):
                                getattr(w, 'field_type_string', '')
                            ftype_str = ''.lower()
                            if not ftype in (2, 5):
                                ftype in (2, 5)
                                if not 'checkbox' in ftype_str:
                                    'checkbox' in ftype_str
                            is_check = 'radio' in ftype_str
                            if not is_check:
                                
                                try:
                                    continue
                                    val = w.field_value
                                    checked = val not in (None, False, '', 'Off', 'off', 'OFF', 0)
                                    if not checked:
                                        
                                        try:
                                            continue
                                            r = w.rect
                                            page_data['ck'].append([
                                                round(r.x0, 1),
                                                round(r.y0, 1),
                                                round(r.x1, 1),
                                                round(r.y1, 1)])
                                            
                                            try:
                                                continue
                                                line['spans']
                                                return page_data
                                                return page_data
                                                block['lines']
                                                blocks
                                                x = None
                                                x = None
                                                x = None
                                                continue
                                                
                                                except Exception:
                                                    None, None, None
                                                    None = None
                                                continue
                                                except Exception:
                                                    None, None, None
                                                    
                                                    try:
                                                        continue
                                                        
                                                        try:
                                                            pass

                                                        return page_data







        
        def _optimize_image(img_bytes, w, h, params):
            '''Optimise une image. Retourne (bytes, orig_w, orig_h) ou None.'''
            
            try:
                img = Image.open(io.BytesIO(img_bytes))
                if img.mode == 'RGBA':
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    bg.paste(img, mask = img.split()[3])
                    img = bg
                elif img.mode != 'RGB':
                    pass
                img = img.convert('RGB')
                if w > 900 and h > 900:
                    max_dim = params['map_max_dim']
                elif w > 1000 and h < 200:
                    new_w = min(w, params['img_max_dim'])
                    new_h = min(h, params['banner_max_h'])
                    if new_w != w or new_h != h:
                        pass
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                elif w < 300 and h < 300:
                    max_dim = 100
                    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                else:
                    max_dim = params['img_max_dim']
                if not w > 1000 or h < 200:
                    max_dim_val = params.get('img_max_dim', 800)
                    if w > 900 and h > 900:
                        max_dim_val = params['map_max_dim']
                    elif w < 300 and h < 300:
                        pass
                    max_dim_val = 100
                    if max(img.size) > max_dim_val:
                        pass
                img.thumbnail((max_dim_val, max_dim_val), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format = 'JPEG', quality = params['jpeg_quality'], optimize = True)
                return (buf.getvalue(), w, h)


        
        def _check_dependencies():
            '''Vérifie que les dépendances sont disponibles.'''
            if not FITZ_AVAILABLE:
                pass
            raise RuntimeError('PyMuPDF requis: pip install pymupdf')
            if not PIL_AVAILABLE:
                pass
            raise RuntimeError('Pillow requis: pip install Pillow')

        
        def _progress(callback, step, total, message):
            '''Appelle le callback de progression si défini.'''
            if callback:
                
                try:
                    callback(step, total, message)
                    return None
                    return None

                return None

        
        def _estimate_times(size_bytes):
            '''Estime les temps de transmission pour chaque mode radio.'''
            result = { }
            size_bits = size_bytes * 8
            for mode, bps in RADIO_BITRATES.items():
                seconds = size_bits / bps
                if seconds > 3600:
                    result[mode] = f'''{seconds / 3600:.1f}h'''
                    continue
                if seconds > 60:
                    result[mode] = f'''{seconds / 60:.1f}min'''
                    continue
                result[mode] = f'''{seconds:.0f}s'''
            return result

        
        def _color_to_hex(color):
            '''Convertit une couleur PyMuPDF en hex #rrggbb.'''
            if not color is not None:
                pass
            return ''
            if isinstance(color, (list, tuple)) and len(color) == 3:
                for None in :
                    pass
                (r, g, b) = c,
            return f'''#{r:02x}{g:02x}{b:02x}'''
            return ''
            

        
        def _hex_to_rgb(hex_color):
            '''Convertit #rrggbb en tuple (r, g, b) normalisé 0-1.'''
            if hex_color or len(hex_color) < 7:
                pass
            return None
            
            try:
                r = int(hex_color[1:3], 16) / 255
                g = int(hex_color[3:5], 16) / 255
                b = int(hex_color[5:7], 16) / 255
                return (r, g, b)


        if __name__ == '__main__':
            import sys
            if len(sys.argv) < 2:
                print(f'''pdf_trans.py v{PDF_TRANS_VERSION}''')
                print('Bibliothèque de transfert PDF par radio pour TCQ')
                print('')
                print('Usage: python pdf_trans.py <fichier.pdf> [quality] [mode]')
                print(f'''  quality: {', '.join(QUALITY_PRESETS.keys())}''')
                print('  mode   : image (défaut), struct')
                print('')
                print('Exemple: python pdf_trans.py PSDI.pdf low image')
            sys.exit(0)
            pdf_path = sys.argv[1]
            quality = sys.argv[2] if len(sys.argv) > 2 else QUALITY_MEDIUM
            mode = sys.argv[3] if len(sys.argv) > 3 else 'image'
            logging.basicConfig(level = logging.INFO, format = '%(asctime)s [%(name)s] %(message)s')
            print(f'''{'============================================================'}''')
            print(f'''  pdf_trans.py v{PDF_TRANS_VERSION} - Test autonome''')
            print(f'''{'============================================================'}''')
            print(f'''  PDF     : {pdf_path}''')
            print(f'''  Qualité : {quality} ({QUALITY_PRESETS[quality]['description']})''')
            print(f'''  Mode    : {mode}''')
            print()
            
            def progress(step, total, msg):
                print(f'''  [{step}/{total}] {msg}''')

            (archive_bytes, info) = pdf_to_archive(pdf_path, quality = quality, mode = mode, progress_callback = progress)
            print('\n  Résultat compression:')
            print(f'''    Original : {info['original_size']:>10,} octets''')
            print(f'''    Archive  : {info['archive_size']:>10,} octets ({info['ratio_percent']}%)''')
            print(f'''    Temps    : {info['compression_time']}s''')
            validation = validate_archive(archive_bytes)
            print(f'''    Valide   : {validation['valid']}, CRC: {validation['checksum_ok']}''')
            (msg_id, fragments, frag_info) = prepare_for_tnc(archive_bytes)
            print('\n  TNC Packet:')
            print(f'''    Fragments: {frag_info['total_fragments']}''')
            print(f'''    Taille air: {frag_info['total_air_bytes']:,} octets''')
            (vara_frame, vara_info) = prepare_for_vara(archive_bytes)
            print('\n  VARA:')
            print(f'''    Trame    : {vara_info['frame_size']:,} octets''')
            print(f'''    CRC32    : {vara_info['crc32']}''')
            print('\n  Recomposition PDF...')
            (pdf_bytes, rebuild_info) = archive_to_pdf(archive_bytes, progress_callback = progress)
            print(f'''    PDF recomposé: {len(pdf_bytes):,} octets''')
            print('\n  Test round-trip TNC...')
            for None in :
                pass
            f, reasm_frags = fragments, { }, 
            (archive_back, reasm_info) = reassemble_from_tnc(reasm_frags, len(fragments))
            if not archive_back == archive_bytes:
                pass
            raise 'ERREUR: round-trip TNC a échoué!'()
        print('    ✅ Round-trip TNC OK')
        print('\n  Test round-trip VARA...')
        (archive_back2, decode_info) = decode_from_vara(vara_frame)
        if not archive_back2 == archive_bytes:
            pass
        raise 'ERREUR: round-trip VARA a échoué!'()
        print(f'''    ✅ Round-trip VARA OK (CRC: {decode_info['crc_valid']})''')
        print('\n  Estimations de transmission:')
        for radio_mode, est_time in info['estimates'].items():
            print(f'''    {radio_mode:35s}: {est_time}''')
        AssertionError
        out_archive = pdf_path.rsplit('.', 1)[0] + '.psdi'
        f = open(out_archive, 'wb')
        f.write(archive_bytes)
        None(None, None)
        out_pdf = pdf_path.rsplit('.', 1)[0] + '_rebuilt.pdf'
        f = open(out_pdf, 'wb')
        f.write(pdf_bytes)
        None(None, None)
        print('\n  Fichiers sauvegardés:')
        print(f'''    Archive : {out_archive} ({len(archive_bytes):,} octets)''')
        print(f'''    PDF     : {out_pdf} ({len(pdf_bytes):,} octets)''')
        print(f'''\n{'============================================================'}''')
        return None
        return None
        except ImportError:
            FITZ_AVAILABLE = False
        continue
    except ImportError:
        PIL_AVAILABLE = False

    continue
    
    with None:
        if not f,:
            pass

None, None, None
continue
with None:
    if not None:
        pass
continue
