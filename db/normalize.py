"""Normalización de estados de México para el inventario.

Resuelve tres problemas vistos en la data del scraper:
1. Sinónimos del mismo estado (CDMX / Ciudad de Mexico / CD. de México → "Ciudad de México").
2. Estados sin valor pero con municipio/colonia que delata el estado (Polanco → CDMX).
3. Ruido no-MX (Texas, Montevideo, Bogotá…) → se deja sin estado MX (None).

Lo usa el loader (futuras cargas) y db/normalize_states.py (data ya cargada).
"""
from __future__ import annotations

import unicodedata

# 32 entidades oficiales (forma canónica con acentos correctos).
OFFICIAL = [
    "Aguascalientes", "Baja California", "Baja California Sur", "Campeche",
    "Chiapas", "Chihuahua", "Ciudad de México", "Coahuila", "Colima", "Durango",
    "Estado de México", "Guanajuato", "Guerrero", "Hidalgo", "Jalisco",
    "Michoacán", "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla",
    "Querétaro", "Quintana Roo", "San Luis Potosí", "Sinaloa", "Sonora",
    "Tabasco", "Tamaulipas", "Tlaxcala", "Veracruz", "Yucatán", "Zacatecas",
]


def _key(s: str) -> str:
    """Clave de comparación: sin acentos, minúsculas, sin puntos, espacios colapsados."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.replace(".", "").lower().split())  # "N.L." → "nl", "CD. de Méx" → "cd de mex"


# Mapa clave-normalizada → estado canónico.
_OFFICIAL_KEYS = {_key(s): s for s in OFFICIAL}

# Sinónimos / abreviaturas frecuentes.
_SYNONYMS = {
    "cdmx": "Ciudad de México",
    "df": "Ciudad de México",
    "distrito federal": "Ciudad de México",
    "cd de mexico": "Ciudad de México",
    "nl": "Nuevo León",
    "mexico": "Estado de México",          # 'México' como estado = Edomex
    "edo de mexico": "Estado de México",
    "edomex": "Estado de México",
    "edo mex": "Estado de México",
    "qro": "Querétaro",
    "bcs": "Baja California Sur",
    "bc": "Baja California",
    "slp": "San Luis Potosí",
    "san miguel de allende": "Guanajuato",  # ciudad, no estado
    "coahuila de zaragoza": "Coahuila",
    "veracruz de ignacio de la llave": "Veracruz",
    "michoacan de ocampo": "Michoacán",
}

# Ciudades/colonias inequívocas → estado (para rescatar los "sin estado").
_CITY_TO_STATE = {
    # CDMX
    "polanco": "Ciudad de México", "san angel": "Ciudad de México",
    "bosques de las lomas": "Ciudad de México", "bosque de las lomas": "Ciudad de México",
    "santa fe": "Ciudad de México", "coyoacan": "Ciudad de México",
    "del valle": "Ciudad de México", "roma norte": "Ciudad de México",
    "condesa": "Ciudad de México", "benito juarez": "Ciudad de México",
    # Quintana Roo
    "tulum": "Quintana Roo", "cancun": "Quintana Roo",
    "puerto morelos": "Quintana Roo", "playa del carmen": "Quintana Roo",
    "bahia de petempich": "Quintana Roo",
    # Yucatán
    "merida": "Yucatán", "temozon norte": "Yucatán",
    # Nuevo León
    "monterrey": "Nuevo León", "san pedro garza garcia": "Nuevo León",
    # Jalisco
    "guadalajara": "Jalisco", "zapopan": "Jalisco", "puerto vallarta": "Jalisco",
    # otros
    "tampico": "Tamaulipas", "acapulco": "Guerrero", "leon": "Guanajuato",
    "queretaro": "Querétaro", "puebla": "Puebla", "tijuana": "Baja California",
}


def normalize_state(state, municipality=None, neighborhood=None):
    """Devuelve el estado canónico de MX, o None si no se reconoce / es no-MX.

    Si `state` viene vacío, intenta rescatarlo del municipio o la colonia.
    Si `state` no es un estado MX reconocido (ruido extranjero), devuelve None.
    """
    s = (state or "").strip()
    if s:
        k = _key(s)
        if k in _SYNONYMS:
            return _SYNONYMS[k]
        if k in _OFFICIAL_KEYS:
            return _OFFICIAL_KEYS[k]
        return s  # valor no reconocido (posible no-MX): se deja sin tocar
    # state vacío: rescatar por municipio/colonia
    for field in (municipality, neighborhood):
        if field:
            fk = _key(field)
            if fk in _CITY_TO_STATE:
                return _CITY_TO_STATE[fk]
    return None
