"""Compact, versioned writing directions adapted from the user's documentary brief.

Prompt rules are not model training, factual verification, or a platform guarantee.
"""
VERSION = 'documentary-localisation-v2'
# Same ordering as rewrite.LANG_INFO; include all UI languages without guessing by letters.
LANGUAGES = {
 'английском':'en','китайском':'zh','хинди':'hi','испанском':'es','арабском':'ar',
 'французском':'fr','бенгальском':'bn','португальском':'pt','русском':'ru','урду':'ur',
 'индонезийском':'id','немецком':'de','японском':'ja','турецком':'tr','вьетнамском':'vi',
 'корейском':'ko','итальянском':'it','персидском':'fa','польском':'pl','украинском':'uk',
 'голландском':'nl','румынском':'ro','греческом':'el','чешском':'cs','венгерском':'hu',
 'шведском':'sv','казахском':'kk','узбекском':'uz','азербайджанском':'az','иврите':'he',
 'финском':'fi','датском':'da','норвежском':'no'}
NATIVE = {
 'ro':'Use idiomatic spoken Romanian with natural clitics, articles, agreement and breath groups. Use modern Romanian diacritics ă, â, î, ș, ț (not cedilla substitutions). Avoid Russian word order, literal calques and unnecessarily formal connective phrases. Write numbers and abbreviations for clear Romanian speech without altering their values.',
 'ru':'Use idiomatic spoken Russian, natural information order, clear referents and varied short/long phrases. Avoid English calques and strings of heavy nominalisations.',
 'en':'Use natural spoken English, clear subject/action, contractions where appropriate and idiomatic transitions. Avoid Russian word order, literal idioms and an artificially solemn register.',
 'de':'Use native German clause order, keep verb-final clauses easy to follow aloud, and split deeply nested subordinate clauses. Preserve idiomatic compounds where clear.',
 'fr':'Use natural spoken French phrasing, short clear clause groups and idiomatic links. Avoid literal source syntax and excessive formal nominal constructions.',
 'es':'Use idiomatic spoken Spanish, natural subject omission and clause rhythm; avoid literal source idioms and needlessly ornate connectors.',
 'pt':'Use natural Portuguese syntax and oral rhythm; do not mix Brazilian and European usages within one narration or invent a region that was not requested.',
 'ja':'Use natural Japanese information order and topic markers, consistent politeness and short breath groups. Do not transfer Western sentence structure or unnecessary pronouns.',
 'zh':'Use natural spoken Mandarin structure, clear topic chains and concise clauses; avoid translationese, excessive pronouns and word-for-word source idioms.',
 'ko':'Use natural Korean clause order and consistent speech level. Keep long embedded clauses easy to follow aloud rather than mirroring source syntax.',
 'ar':'Use clear, idiomatic Modern Standard Arabic unless a dialect was explicitly requested. Use natural clause order and oral cadence; do not mix dialects.',
 'hi':'Use idiomatic spoken Hindi with natural clause order and clear referents; avoid mechanical English constructions and unnecessary code-switching.',
 'pl':'Use idiomatic spoken Polish, natural information structure and inflection. Avoid Russian/English calques and overly bureaucratic phrasing.'}


def language_code(value):
    key=str(value or '').strip().casefold()
    if key in LANGUAGES:return LANGUAGES[key]
    key=key.replace('_','-').split('-')[0]
    return key if key in set(LANGUAGES.values()) else None


LOCALES = {
 'ro-RO':('ro','Румынский · Румыния','Standard Romanian as spoken in Romania; native Romanian vowels and consonants, clear ș and ț, natural word stress. No French or Russian accent.'),
 'es-MX':('es','Испанский · Латинская Америка','Neutral Latin American Spanish, Mexican standard pronunciation and vocabulary; seseo, ustedes, no vosotros or Peninsular ceceo. Do not imitate a Russian or French accent.'),
 'es-ES':('es','Испанский · Испания','Standard Spanish of Spain; consistent Peninsular vocabulary, vosotros where appropriate and native distinction of s versus c/z. Do not mix Latin American delivery.'),
 'pt-BR':('pt','Португальский · Бразилия','Natural Brazilian Portuguese, consistent Brazilian vocabulary, pronoun use and native vowel rhythm. Avoid European vowel reduction and Russian or French accent.'),
 'pt-PT':('pt','Португальский · Португалия','Native European Portuguese, consistent European vocabulary, clitic placement, natural vowel reduction and phrase rhythm. Do not mix Brazilian pronunciation or syntax.')}
DEFAULT_LOCALES={'ro':'ro-RO','es':'es-MX','pt':'pt-BR'}


def locale_for(language, cfg=None, locale=None):
    code=language_code(language)
    selected=locale or (cfg or {}).get('speech_locales',{}).get(code) or DEFAULT_LOCALES.get(code)
    if selected and (selected not in LOCALES or LOCALES[selected][0]!=code):
        raise ValueError('Регион озвучки не соответствует выбранному языку.')
    return selected


def localisation(language=None, locale=None):
    code=language_code(language)
    return ('NATIVE-LANGUAGE NARRATION ADAPTATION. Preserve meaning, supported facts, dramatic purpose and the '
            'source presentation character, not its sentence syntax. Rebuild sentences, idioms, transitions and '
            'spoken dialogue for native listeners in the requested language. Adapt sentence length, word order, '
            'information emphasis and breath groups; literal translation is not the task. Never change a factual '
            'example into an invented local event. Explain unfamiliar cultural comparisons or use an explicitly '
            'hypothetical equivalent. Write numbers, units and abbreviations naturally for speech; preserve their '
            'values. Proper names and terminology need pronunciation review. Do not insert SSML, stress marks, '
            '[pause], [music] or stage directions into the spoken script. Punctuation may indicate natural pauses. '
            'Do not assume identical words-per-minute, emotional intensity or pause lengths across languages. '
            + NATIVE.get(code,'Use native idiomatic syntax, consistent register and the natural oral cadence of the requested language.')+' '
            + (LOCALES[locale_for(language,locale=locale)][2]+' ' if code in DEFAULT_LOCALES else ''))


def writing(stage='script', language=None, locale=None):
    common=('DOCUMENTARY WRITING RULES '+VERSION+'. Follow the useful presentation of the reference: '
            'tone, humour or seriousness, explanatory clarity, viewpoint and shifts of emotional intensity. '
            'Build curiosity from a real question and meaningful discoveries, never fake cliffhangers or unsupported sensation. '
            'Facts are not independently verified merely because they occur in the reference. Distinguish observations, '
            'hypotheses and interpretation. Never invent records, dates, scientific findings, accidents, quotations or tragedy. ')
    if stage=='plan':
        return common+('Plan a sensory opening and a genuine supported paradox/question, a causal chain of discoveries, '
            'mechanisms shown through action, a grounded change of perspective when the material supports one, '
            'and a circular ending that resolves the opening. Each block needs a purpose, discovery and bridge; '
            'adapt to topic, reference genre and duration. Do not force a biological/history template or 8–10 blocks '
            'into a short trial. A twist must be prepared earlier and must not be manufactured. Preserve the reference\'s '
            'broad visual medium, abstraction and way of explaining ideas while creating own designs and compositions. ')
    return common+localisation(language,locale)+(
        'For the full story, begin with a concrete image/action and establish the main question early. '
        'Explain problem → mechanism → consequence through micro-scenes rather than a list of facts. '
        'Give information-dense passages breathing room through a supported example, contrast or question. '
        'Connect paragraphs by cause and consequence, varying the language of transitions. '
        'Only the opening section starts the story; later sections continue it. Only the final section closes it '
        'and returns to the opening image/question. Do not restart the hook in every section. '
        'Before returning, silently edit for unsupported causality, ambiguous referents, repeated transitions, '
        'translationese, heavy clauses and filler without removing useful explanations. '
        'Return clean narration in the requested output schema; keep factual uncertainties and production notes separate. ')


def voice_direction(delivery, language):
    """Use relative energy/rhythm, not a literal foreign WPM or accent."""
    if not delivery:return None
    import copy
    result=copy.deepcopy(delivery)
    pace=float(delivery.get('tempo_wpm',150))
    result['tempo_wpm']=141 if pace<120 else 159 if pace>175 else 150
    result['instructions']=(str(delivery.get('instructions',''))[:1600]+' Preserve relative restraint, energy and emphasis, but use native '+str(language)+' prosody, phrase endings and breath groups. Do not imitate the source accent or mechanically copy its words-per-minute.')[:2000]
    return result


def wrong_script(text,language):
    """Guard obvious alphabet mismatches, not language identification or fluency."""
    code=language_code(language);letters=[ch for ch in text if ch.isalpha()]
    if not code or len(letters)<40:return False
    if code in ('ru','uk','kk'):check=lambda ch:'\u0400'<=ch<='\u04ff'
    elif code in ('zh','ja'):check=lambda ch:'\u3400'<=ch<='\u9fff' or (code=='ja' and '\u3040'<=ch<='\u30ff')
    elif code=='ko':check=lambda ch:'\uac00'<=ch<='\ud7af'
    elif code in ('ar','fa','ur'):check=lambda ch:'\u0600'<=ch<='\u06ff'
    elif code=='he':check=lambda ch:'\u0590'<=ch<='\u05ff'
    elif code=='hi':check=lambda ch:'\u0900'<=ch<='\u097f'
    elif code=='bn':check=lambda ch:'\u0980'<=ch<='\u09ff'
    elif code=='el':check=lambda ch:'\u0370'<=ch<='\u03ff'
    else:check=lambda ch:'a'<=ch.casefold()<='z' or '\u00c0'<=ch<='\u024f' or '\u1e00'<=ch<='\u1eff'
    return sum(check(ch) for ch in letters)/len(letters)<.4
