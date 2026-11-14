"""Drama v2, images stage: the serial-grammar shot list and its image prompts (2026-11-01).

usage: v2_prompts.py            # write v2/visuals.json + v2/IMAGE_PROMPTS.md, print the checks
       v2_prompts.py --check    # checks only

Plan: docs/DRAMA_PIPELINE_V2_2026-11-01.md §2.1–§2.3. The audio stage is reused as-is (audio/lines/*.wav,
the chant bed) and so is the user's Google Flow clip for Shiva's last line (clips/provided/c15a_shiva_flow.mp4).

A prompt is assembled from four independent parts, so a mood never leaks into a face and a face never brings
its own expression:
  framing + action      what the camera sees in this shot
  EXPRESSION            the character's face and body, stated physically (brows, eyes, mouth, tears)
  IDENTITY + wardrobe   who they are, with every expression word removed (the old bible blocks said "warm
                        friendly smile", which is how grief stills drifted into smiles)
  SCENE mood            light, palette and atmosphere for the whole scene, shared by its shots

Each line's audio is laid out over shots as segments (line id, from s, to s). A lip-sync shot carries one
segment of one speaker's line, at most one InfiniteTalk window (5.06 s); the rest of a long line plays over
reactions or inserts (L/J-cuts). Prayers and inner lines are heard over living stills with a closed mouth.
"""

import json
import subprocess
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1]
OUT = P / "v2"
WINDOW = 5.06  # one InfiniteTalk window
LIPSYNC = {"speak", "ots"}
NO_FACE_MOTION = {"establishing", "insert"}  # camera-only moves allowed only here

LOOK = ("Photoreal cinematic film still, 35mm, natural skin texture with pores, shallow depth of field, "
        "true-to-life Indian skin tones, clean bright colours. Wide 16:9 frame. No text, no letters, no watermark.")
LOOK_EMPTY = ("Photoreal cinematic film still, 35mm, shallow depth of field, clean bright colours. Wide 16:9 "
              "frame. No text, no letters, no watermark.")
AVOID = ("a different person, changed face, copied reference pose or clothing, dark or gloomy light, horror, "
         "text, letters, watermark, logo, deformed hands, extra fingers, plastic skin, cartoon")
NO_SMILE = "smiling, grin, happy expression, showing teeth"
# Expressions Kling O3 overrode with the reference's pleasant face (sample + A/B, 2026-11-01).
EMOTIONAL = {"grave", "worried", "dread", "crying_out", "praying_tears", "fear_father", "sobbing", "broken",
             "heartbroken_quiet", "terrified_couple", "scared", "stern", "divine_command", "shock", "panic", "alarm",
             "unease", "alarmed_masked", "commanding_masked", "remembering", "stunned_relief", "eyes_widen",
             "grateful_tears", "joy_tears"}
GRIEF_RISK = {"worried", "crying_out", "fear_father", "heartbroken_quiet", "scared"}
OPEN_MOUTH = {"crying_out"}  # a scream starts open; every other lip-sync still starts with the mouth closed

# Identity only: no expression words. Built from the series bibles and shots.json["characters"].
IDENTITY = {
    "abhinav": "Abhinav, a handsome 25-year-old Telugu man, oval face with a defined jawline, warm dark-brown eyes, "
               "thick black hair neatly styled with a soft side part, light neatly trimmed stubble, warm wheatish skin, "
               "slim fit build, a brown rudraksha bead bracelet on a red thread on his right wrist",
    "lakshmi": "Lakshmi, a 50-year-old Telugu mother, soft round face, large dark eyes with fine lines, long black hair "
               "with a few grey strands in a neat low bun, small round red bindi, kumkum in the hair parting, thin gold "
               "chain with a mangalsutram, green and gold glass bangles, small gold stud earrings, medium-brown skin, "
               "slightly plump",
    "raghava": "Raghava, a 56-year-old Telugu father, lean face, receding short grey hair combed back, thick grey "
               "moustache, clean-shaven chin, three horizontal white vibhuti stripes on the forehead with a small red "
               "kumkum dot, a rudraksha mala around the neck, steel wristwatch, medium-dark brown skin, lean build",
    "doctor": "Dr. Srinivas, a 45-year-old Indian male doctor, short black hair greying at the temples, rimless glasses",
    "grandfather": "Tatayya, a 75-year-old thin Telugu man, white hair, white stubble, vibhuti stripes on the forehead, "
                   "a rudraksha mala",
    "nurse1": "Nurse Swathi, a 28-year-old Indian woman, hair tied back in a neat bun",
    "nurse2": "Nurse Rani, a 35-year-old Indian woman",
    "passerby": "an auto-rickshaw driver, a 40-year-old Telugu man, thick black moustache, sunburnt face",
    "yama1": "Yama Dhootha, a tall divine warrior with dark-blue skin, stern dignified face, black-and-gold armour and "
             "crown",
    "yama2": "the second Yama Dhootha, a tall divine warrior with dark-blue skin, stern dignified face, black-and-gold "
             "armour and crown",
    "shiva": "Lord Shiva in classic devotional TV-serial style, photoreal, young handsome face, solid pale blue-grey "
             "skin, warm brown eyes, three white vibhuti lines with a vertical third-eye mark and an orange-red dot, long "
             "dark-brown matted locks in a high top knot wrapped with rudraksha beads, white crescent moon in the hair, "
             "large gold hoop earrings, grey-green cobra coiled around the neck with its hood over the right shoulder, "
             "rudraksha malas and armbands, tiger-skin wrap with an orange-red sash, silver-gold trishul with a small "
             "damaru",
    "girl": "a five-year-old Telugu girl in a yellow frock, two small pigtails",
}

# Wardrobe by state; a shot names the state when it differs from the character's default.
WARDROBE = {
    "abhinav": {"home": "a light-blue slim-fit formal shirt with sleeves folded to the forearm, dark-grey trousers, "
                        "a black wristwatch on his left wrist",
                "ride": "a light-blue formal shirt, dark-grey trousers, a black open-face helmet",
                "injured": "a torn light-blue shirt, unconscious",
                "ot": "covered to the chest with a green surgical sheet, an oxygen mask, eyes closed",
                "soul": "in soul form: a plain white kurta, softly glowing and slightly translucent, no injuries",
                "bed": "a pale-blue hospital gown, a white bandage around his head, an IV line taped to his left hand"},
    "lakshmi": {"home": "her maroon cotton saree with a thin gold border"},
    "raghava": {"home": "a cream half-sleeve cotton shirt and white dhoti-style trousers"},
    "doctor": {"home": "a white coat over blue scrubs, a stethoscope around his neck",
               "ot": "blue surgical scrubs, surgical cap, a surgical mask over his nose and mouth",
               "out": "blue scrubs, surgical cap pushed back, the mask pulled down under his chin"},
    "nurse1": {"home": "a light-blue nurse uniform with an ID badge"},
    "nurse2": {"home": "a light-blue nurse uniform", "ot": "light-blue scrubs, surgical cap, a surgical mask"},
    "grandfather": {"home": "a white dhoti and angavastram"},
    "passerby": {"home": "a khaki auto-driver shirt with a checked towel over his shoulder"},
    "yama1": {"home": "holding a coiled rope noose"},
    "yama2": {"home": "holding a heavy golden mace"},
    "shiva": {"home": "exactly as in his reference images"},
    "girl": {"home": ""},
}

# Scene moods: setting, light, palette, atmosphere. Shared by every shot in the scene.
SCENE = {
    1: ("Home, morning", "A modest, cheerful middle-class Hyderabad home: cream walls, a small pooja shelf with a brass "
        "lamp, a mango-leaf toran over the door, potted tulsi. Golden sunrise light streams through the windows in soft "
        "warm shafts. Palette amber, cream and leaf green. Mood: joyful, tender, auspicious."),
    2: ("Road, the accident", "A busy, sunny Hyderabad road near the Ameerpet signal: shops, trees, auto-rickshaws, a "
        "metro pillar. Hard bright midday sun, clear blue sky, vivid street colours. Mood: lively, turning sharply "
        "tense."),
    3: ("Road, aftermath", "The same sunny road moments later: traffic stopped, a small crowd gathering, dust in the "
        "air. Bright, flat noon light, no shadows of drama, colours slightly bleached by the sun. Mood: shock, urgency."),
    4: ("Ambulance to hospital", "A white 108 ambulance and a modern private hospital's emergency entrance in "
        "daylight: glass doors, white and pastel-blue walls. Bright daylight with red-and-blue emergency flashes. "
        "Mood: urgent, racing against time."),
    5: ("The phone call", "The family's sunlit home kitchen: steel vessels on a shelf, a gas stove, a calendar of a "
        "deity, warm late-morning light through a small window. Mood: ordinary calm breaking into dread."),
    "5h": ("The phone call", "A bright hospital nursing station: white counter, a landline phone, files, pastel-blue "
           "walls, cool clean light. Mood: professional calm carrying bad news."),
    6: ("Rushing to the hospital", "A sunny city road at midday, wind in the clothes, traffic around an old grey Bajaj "
        "scooter. Bright light. Mood: frantic, praying."),
    7: ("Doctor and consent", "A clean, bright modern private hospital corridor outside the operating theatre: warm "
        "white light, pastel-green walls, a potted plant, steel benches. Mood: hushed tension, fear held in."),
    8: ("Prayer and tears", "A quiet prayer alcove at the end of the hospital corridor: a framed picture of Lord Shiva "
        "garlanded with marigolds, a small brass diya burning, incense smoke. Soft warm lamp glow against the bright "
        "white daylight of the corridor. Mood: devotional, aching, intimate."),
    9: ("Operation critical", "Inside a bright modern operating theatre: intense white surgical lights, blue-green "
        "scrubs, steel instruments, patient monitors. Mood: urgent, frightening, clinical but bright."),
    "9c": ("Operation critical", "The bright hospital corridor outside the operating theatre: steel benches, "
           "pastel-green walls, a glowing red lamp above the theatre doors. Mood: helpless waiting, fear."),
    10: ("Flashback: Tatayya's words", "Twenty-five years ago: the veranda of a village house with a tiled roof and "
         "wooden pillars, afternoon sun. Warm golden-sepia glow, soft haze and slightly softened edges like a memory. "
         "Mood: nostalgic, wise, tender."),
    "10p": ("Flashback: Tatayya's words", "The bright present-day hospital corridor, pastel-green walls, steel "
            "benches; a soft warm glow begins to touch his face like a memory. Mood: remembering, tender."),
    11: ("The chant", "The hospital prayer alcove with the Shiva picture and the diya; golden sunrays begin to slant "
         "through the corridor window as the chant builds. Mood: faith, stillness, rising hope."),
    12: ("Journey of the soul", "A luminous otherworldly space between earth and heaven: soft white-gold clouds of "
         "light, a faint path of light, gentle sparkles. Bright and ethereal, never dark. The Yama Dhoothas' dark blue "
         "stands out against the gold. Mood: awe, fear turning to grace."),
    13: ("The miracle", "The hospital corridor outside the operating theatre, now flooded with bright morning light "
         "through the windows. Mood: disbelief turning into overwhelming relief."),
    14: ("Recovery room", "A bright, airy private recovery room: big window with morning sun, a plant, white sheets, "
         "monitors showing steady green lines. Mood: hopeful, tender, gentle."),
    15: ("The divine voice", "Reused Google Flow clip (clips/provided/c15a_shiva_flow.mp4)."),
    16: ("End card", "Divine gold and white light, soft bokeh, devotional. Mood: inviting, a hint of suspense."),
}

# Physical expressions, reused by name so the same emotion is phrased the same way every time.
EXPR = {
    "loving": "eyes soft and shining with love, a gentle closed-mouth smile, relaxed brow",
    "proud_smile": "a warm proud closed-mouth smile, eyes crinkled at the corners",
    "earnest": "bright eager eyes, eyebrows slightly raised, lips closed about to speak, a hint of a smile",
    "caring": "a kind, slightly concerned look, eyebrows gently raised, lips closed",
    "cheerful": "a broad happy smile, eyes bright",
    "unease": "a faint frown, eyes searching, lips pressed together",
    "shock": "eyes wide open, eyebrows shot up, mouth slightly open in shock, face frozen",
    "panic": "eyes wide and darting, brow furrowed hard, mouth tense, sweat on the forehead",
    "grave": "a grave, sympathetic face, brows drawn together, eyes heavy, mouth closed with the corners turned down, "
             "no smile",
    "worried": "brows drawn together and raised in the middle, eyes anxious, lips pressed together, no smile",
    "alarm": "eyes wide open, eyebrows raised high, face tense with alarm, lips closed",
    "dread": "the colour draining from her face, eyes widening and glistening, lips trembling apart, no smile",
    "crying_out": "face contorted with shock and grief, mouth open in a cry, tears running down her cheeks, no smile",
    "praying_tears": "anguished weeping: eyes squeezed shut, wet tear streaks shining down both cheeks, brows "
                     "knotted upward in pain, mouth pulled down at the corners and trembling, nose and eyes red, no "
                     "smile, not serene, not peaceful",
    "fear_father": "terrified: eyes red-rimmed, wide and wet with fear, brows raised and knotted together, deep "
                   "forehead creases, jaw clenched and trembling, mouth closed and turned down, no smile",
    "sobbing": "face crumpled in grief, eyes swollen and red, tears on her cheeks, mouth closed and trembling, no smile",
    "broken": "eyes red and wet, looking upward, brows knotted in pain, mouth closed and quivering, a tear rolling "
              "down, no smile",
    "heartbroken_quiet": "a distant hollow gaze, wet eyes, a single tear, mouth closed, no smile",
    "alarmed_masked": "eyes wide with alarm above the surgical mask, brows raised",
    "commanding_masked": "eyes sharp and focused above the surgical mask, brows lowered",
    "terrified_couple": "both faces stricken with terror: eyes wide, red and wet, brows raised and knotted, mouths "
                        "closed and turned down, gripping each other's hands, no smile, not posing",
    "remembering": "a distant, inward gaze, eyes glistening, brows softening, mouth closed",
    "wise": "a gentle, wise and serious face, calm eyes looking straight at the listener, lips closed",
    "devout": "eyes closed, face calm and devoted, brows relaxed, lips closed",
    "chant_join": "eyes closed, hands folded in prayer, tears on her cheeks, lips moving softly in prayer, no "
                  "smile",
    "stern": "a stern, dignified, unsmiling face, eyes steady and cold",
    "scared": "eyes wide with fear, brows raised and pinched, lips parted, no smile",
    "divine_command": "a calm, powerful, authoritative face, eyes steady and commanding, lips closed, no smile",
    "divine_tender": "a calm, compassionate face, soft eyes, the faintest gentle smile",
    "bowing": "heads bowed low in respect, eyes lowered",
    "astonished_joy": "eyes wide with astonishment, eyebrows raised, a disbelieving smile breaking out",
    "stunned_relief": "faces frozen in disbelief, then eyes filling with tears of relief, mouths slightly open",
    "grateful_tears": "eyes wet with tears, looking upward, a trembling grateful closed-mouth smile",
    "joy_tears": "tears of joy on her cheeks, a radiant trembling closed-mouth smile",
    "weak_wonder": "eyes half-open and wondering, a faint weak smile, face pale and tired",
    "eyes_opening": "eyelids just opening, gaze unfocused, face peaceful; normal human eyes, no glow",
    "eyes_widen": "eyes widening with sudden realisation, lips parting slightly, no smile",
    "unconscious": "eyes closed, face slack and still",
}


def S(id, scene, kind, who, framing, action, expr=None, lines=(), cam="", motion="", state=None,
      engine=None, avoid="", secs=None, refs=None, place=None, still=None):
    """One visual. lines: (line_id, from_s, to_s|None). secs: picture length when no line sets it.
    place: a SCENE key overriding the scene's mood when the scene has two locations.
    still: another shot's id whose still this shot reuses (its own lines, length and camera move stay)."""
    return dict(id=id, scene=scene, kind=kind, who=list(who), framing=framing, action=action, expr=expr,
                lines=[list(x) for x in lines], cam=cam, motion=motion, state=state or {}, engine=engine,
                avoid=avoid, secs=secs, refs=refs, place=place, still=still)


E = None  # "to the end of the line"
V = [
    # 1. Home, morning ---------------------------------------------------------------------------------------------
    S("s01", 1, "establishing", [], "Wide exterior", "a small cheerful two-storey house on a quiet Hyderabad lane at "
      "sunrise, rangoli at the gate, a potted tulsi, birds on the wire, golden light on the facade",
      lines=[("c01a_vo", 0, 3.6)], cam="slow push-in, 2.5D parallax"),
    S("s02", 1, "insert", [], "Close-up insert", "the small pooja shelf inside: a brass diya burning, a framed photo of "
      "an old man (Tatayya) with a sandalwood garland, a rudraksha mala beside it", lines=[("c01a_vo", 3.6, E)],
      cam="slow push-in"),
    S("s03", 1, "speak", ["lakshmi"], "Medium close-up, eye level",
      "Lakshmi at the sunny kitchen doorway holding a small steel bowl of curd and sugar and a spoon, looking at her son "
      "just off camera", "loving", lines=[("c01b_line", 0, 4.6)]),
    S("s04", 1, "insert", ["lakshmi", "abhinav"], "Close-up insert on hands",
      "Lakshmi's hand with green glass bangles feeding a spoonful of curd to Abhinav, his face partly in frame, smiling",
      lines=[("c01b_line", 4.6, E)], motion="the spoon moves to his mouth, bangles shift", cam="static"),
    S("s05", 1, "speak", ["abhinav"], "Medium close-up",
      "Abhinav in the bright living room, office bag strap across his chest, speaking to his mother off camera",
      "earnest", lines=[("c01c_line", 0, 4.6)], state={"abhinav": "home"}),
    S("s06", 1, "living", ["lakshmi", "raghava"], "Medium two-shot",
      "Lakshmi and Raghava standing together, listening to their son off camera, exchanging a moved glance",
      "proud_smile", lines=[("c01c_line", 4.6, E)], motion="they glance at each other, Lakshmi's eyes glisten",
      cam="slow push-in"),
    S("s07", 1, "speak", ["raghava"], "Medium close-up",
      "Raghava sitting on a cane chair with a folded Telugu newspaper on his knee, looking up at his son", "caring",
      lines=[("c01d_line", 0, 4.6)]),
    S("s08", 1, "insert", ["abhinav"], "Extreme close-up insert",
      "the brown rudraksha bracelet on a red thread on Abhinav's right wrist, sunlight on the beads",
      lines=[("c01d_line", 4.6, E)], motion="his fingers touch the beads", cam="slow push-in"),
    S("s09", 1, "speak", ["abhinav"], "Medium shot, face clearly visible",
      "Abhinav at the open front door holding a black open-face helmet, turning back to his parents, bright morning "
      "light behind him", "cheerful", lines=[("c01e_line", 0, E)], state={"abhinav": "home"}),
    # 2. Road, the accident -----------------------------------------------------------------------------------------
    S("s10", 2, "action", ["abhinav"], "Wide tracking shot from the front side",
      "Abhinav riding a red motorbike along the sunny busy road, relaxed and happy, eyes on the road ahead", "cheerful",
      lines=[("c02a_vo", 0, E)], state={"abhinav": "ride"}, motion="the bike moves forward through traffic, "
      "camera tracks alongside"),
    S("s11", 2, "action", ["girl"], "Wide street-level shot",
      "a little girl in a yellow frock runs onto the road chasing a red ball, a large lorry approaching in the "
      "distance", secs=3.0, motion="the girl runs into the road after the bouncing ball"),
    S("s12", 2, "hero", ["abhinav", "girl"], "Wide dramatic shot",
      "Abhinav swerving his red motorbike hard to avoid the little girl, the lorry looming behind, motion blur, "
      "no impact shown", "shock", secs=4.0, state={"abhinav": "ride"},
      motion="sharp swerve, the bike tilts, the girl freezes"),
    S("s13", 2, "action", [], "Wide shot", "aftermath on the sunny road: a red motorbike on its side, a young man "
      "lying still on the asphalt in the distance, a streak of blood near his head, people running towards him, the "
      "little girl in her yellow frock safe in her mother's arms at the roadside", secs=3.5, engine="qwen",
      motion="people run in, dust settles"),
    # 3. Passerby ---------------------------------------------------------------------------------------------------
    S("s14", 3, "speak", ["passerby"], "Medium close-up",
      "the auto-rickshaw driver standing by his yellow-green auto, looking towards the accident", "alarm",
      lines=[("c03a_line", 0, E)]),
    S("s15", 3, "speak", ["passerby"], "Medium close-up",
      "the auto driver pressing a phone to his ear, the crowd behind him", "panic",
      lines=[("c03b_line", 0, 4.6)]),
    S("s16", 3, "action", ["abhinav"], "High-angle wide shot",
      "a crowd circling Abhinav lying unconscious on the road beside his fallen red motorbike, one man shading his "
      "face with a newspaper", "unconscious", lines=[("c03b_line", 4.6, E)], state={"abhinav": "injured"},
      motion="the crowd shifts, someone kneels"),
    # 4. Ambulance --------------------------------------------------------------------------------------------------
    S("s17", 4, "action", [], "Low-angle wide shot", "an Indian 108 ambulance (a white Force Traveller van with red and "
      "blue stripes) racing through daytime Hyderabad traffic, lights flashing", lines=[("c04a_vo", 0, 3.4)], motion="the ambulance speeds past, traffic parts"),
    S("s18", 4, "action", ["abhinav"], "Medium wide shot", "two paramedics wheeling Abhinav lying flat and unconscious on a "
      "stretcher through the glass doors of the emergency entrance, his eyes closed, mouth slack, not smiling", "unconscious", lines=[("c04a_vo", 3.4, 6.4)],
      state={"abhinav": "injured"}, motion="the stretcher rushes towards camera"),
    S("s19", 4, "insert", ["abhinav"], "Extreme close-up insert", "only a limp right hand hanging off the edge of a "
      "moving stretcher, the brown rudraksha bracelet on its red thread around the wrist; no face in frame", lines=[("c04a_vo", 6.4, E)], motion="the hand sways with the "
      "stretcher", cam="slow push-in"),
    # 5. The phone call ---------------------------------------------------------------------------------------------
    S("s20", 5, "speak", ["nurse1"], "Medium close-up",
      "Nurse Swathi at the bright nursing station holding a landline receiver to her ear", "grave",
      lines=[("c05a_line", 0, 4.6)], avoid=NO_SMILE, place="5h"),
    S("s21", 5, "living", ["lakshmi"], "Medium shot", "Lakshmi in her sunlit kitchen answering a ringing phone, a "
      "steel glass of water in her other hand", "unease", lines=[("c05a_line", 4.6, E)],
      motion="she lifts the phone to her ear, blinks", cam="slow push-in"),
    S("s22", 5, "speak", ["lakshmi"], "Close-up", "Lakshmi holding the phone to her ear in the kitchen", "worried",
      lines=[("c05b_line", 0, E)], avoid=NO_SMILE),
    S("s23", 5, "speak", ["nurse1"], "Close-up", "Nurse Swathi speaking gently into the phone, apologetic",
      "grave", lines=[("c05c_line", 0, 4.6)], avoid=NO_SMILE, place="5h"),
    S("s24", 5, "shock", ["lakshmi"], "Close-up", "Lakshmi listening on the phone as the news sinks in", "dread",
      lines=[("c05c_line", 4.6, E)], motion="her face slowly falls, eyes fill with tears", cam="serial triple zoom",
      avoid=NO_SMILE),
    S("s25", 5, "insert", [], "Low close-up, slow motion", "a steel glass falling from a woman's hand onto the "
      "kitchen floor, water splashing", secs=1.2, motion="the glass hits the floor, water splashes, slow motion",
      engine="flow"),
    S("s26", 5, "speak", ["lakshmi"], "Close-up", "Lakshmi crying out towards the next room, the phone slipping",
      "crying_out", lines=[("c05d_line", 0, E)], avoid=NO_SMILE),
    # 6. Rushing ----------------------------------------------------------------------------------------------------
    S("s27", 6, "action", ["raghava", "lakshmi"], "Wide tracking shot",
      "Raghava riding an old grey Bajaj scooter through traffic, Lakshmi riding pillion behind him clutching his "
      "shoulder, her saree end flying", "fear_father", lines=[("c06a_line", 0, 2.6)],
      motion="the scooter weaves through traffic, wind in their clothes"),
    S("s28", 6, "living", ["lakshmi"], "Close-up on the moving scooter", "Lakshmi on the pillion seat praying as they "
      "ride", "praying_tears", lines=[("c06a_line", 2.6, E)], motion="wind moves her hair, tears roll, lips tremble",
      avoid=NO_SMILE),
    # 7. Doctor and consent -----------------------------------------------------------------------------------------
    S("s29", 7, "establishing", [], "Wide shot", "the bright corridor outside the operating theatre, steel benches, "
      "a potted plant, double doors with a red lamp above", secs=2.0, cam="slow push-in"),
    S("s30", 7, "speak", ["doctor"], "Medium close-up", "Dr. Srinivas in the corridor speaking to the parents off "
      "camera, a file in his hand", "grave", lines=[("c07a_line", 0, 4.6)], avoid=NO_SMILE),
    S("s31", 7, "living", ["raghava", "lakshmi"], "Medium two-shot", "Raghava and Lakshmi standing close together, "
      "hearing the news", "terrified_couple", lines=[("c07a_line", 4.6, E)],
      motion="Lakshmi grips Raghava's arm, his jaw trembles", avoid=NO_SMILE),
    S("s32", 7, "speak", ["raghava"], "Close-up", "Raghava facing the doctor, who is just off camera to the left", "fear_father",
      lines=[("c07b_line", 0, E)], avoid=NO_SMILE),
    S("s33", 7, "speak", ["doctor"], "Medium close-up", "Dr. Srinivas holding out a consent form on a clipboard and a "
      "pen", "grave", lines=[("c07c_line", 0, 4.4)], avoid=NO_SMILE),
    S("s34", 7, "insert", ["raghava"], "Close-up insert on hands", "Raghava's trembling hand with a steel wristwatch "
      "signing the consent form on a clipboard", lines=[("c07c_line", 4.4, E), ("c07d_line", 0, 2.4)],
      motion="the hand trembles as the pen moves"),
    S("s35", 7, "living", ["lakshmi"], "Close-up", "Lakshmi pleading towards the doctor, hands joined", "sobbing",
      lines=[("c07d_line", 2.4, E)], motion="her joined hands shake, a tear falls", avoid=NO_SMILE),
    # 8. Prayer and tears -------------------------------------------------------------------------------------------
    S("s36", 8, "insert", [], "Close-up", "the framed picture of Lord Shiva in the prayer alcove, garlanded with "
      "marigolds, a brass diya flame in front", lines=[("c08a_line", 0, 2.0)], motion="the diya flame flickers, "
      "incense smoke curls", cam="slow push-in", refs=["shiva"]),
    S("s37", 8, "living", ["lakshmi"], "Close-up, profile three-quarter", "Lakshmi kneeling before the Shiva picture, "
      "praying", "praying_tears", lines=[("c08a_line", 2.0, E)], motion="tears roll, her shoulders shake",
      avoid=NO_SMILE),
    S("s38", 8, "living", ["raghava"], "Close-up", "Raghava standing at the alcove with joined hands, looking up at "
      "the picture", "broken", lines=[("c08b_line", 0, E)], motion="a tear rolls down, lips quiver",
      cam="slow push-in", avoid=NO_SMILE),
    S("s39", 8, "speak", ["lakshmi"], "Close-up", "Lakshmi sitting on the corridor bench, speaking quietly to her "
      "husband beside her (off camera)", "heartbroken_quiet", lines=[("c08c_line", 0, 2.6)], avoid=NO_SMILE),
    S("s40", 8, "flashback", ["lakshmi", "abhinav"], "Medium shot, memory", "this morning at home in the sunny kitchen: "
      "Lakshmi feeding Abhinav curd and sugar, both smiling", "loving", lines=[("c08c_line", 2.6, E)],
      state={"abhinav": "home"}, motion="soft, slow, dreamlike", cam="slow drift, memory glow", place=1),
    # 9. Operation critical -----------------------------------------------------------------------------------------
    S("s41", 9, "insert", [], "Close-up", "the red lamp glowing above the operating theatre doors", secs=1.5,
      motion="the red lamp glows steadily", place="9c"),
    S("s42", 9, "living", ["nurse2"], "Close-up inside the theatre", "Nurse Rani beside a patient monitor, looking up "
      "at the doctor", "alarmed_masked", lines=[("c09a_line", 0, E)], state={"nurse2": "ot"},
      motion="her eyes dart to the monitor"),
    S("s43", 9, "insert", [], "Close-up", "a patient monitor with a falling blood-pressure line and a flat pulse "
      "trace, no readable text", secs=1.2, motion="the line drops, alarms flash"),
    S("s44", 9, "living", ["doctor"], "Close-up", "Dr. Srinivas under the surgical lights turning sharply",
      "commanding_masked", lines=[("c09b_line", 0, E)], state={"doctor": "ot"}, motion="he turns, his hand reaches out"),
    S("s45", 9, "action", ["doctor", "nurse2", "abhinav"], "Medium shot", "the masked surgical team at the operating "
      "table as the doctor presses defibrillator paddles to the chest of the patient, Abhinav, who lies unconscious "
      "under a green sheet with an oxygen mask; every member of the team wears a surgical mask and cap", secs=2.5,
      state={"doctor": "ot", "nurse2": "ot", "abhinav": "ot"}, motion="the patient's body jolts, the team leans in"),
    S("s46", 9, "living", ["raghava", "lakshmi"], "Medium two-shot", "outside, Raghava and Lakshmi seen from the side on "
      "the steel bench, both staring at the theatre doors at the right of the frame, not at the camera", "terrified_couple", secs=2.5, motion="Lakshmi clutches her mangalsutram, "
      "Raghava's eyes fill", avoid=NO_SMILE, place="9c"),
    # 10. Flashback -------------------------------------------------------------------------------------------------
    S("s47", 10, "living", ["raghava"], "Close-up", "Raghava on the corridor bench, remembering", "remembering",
      lines=[("c10a_vo", 0, E)], motion="his gaze drifts, the corridor light softens", cam="slow push-in", place="10p",
      still="s49"),  # user, board 2026-11-01: "Remove this and reuse s49"
    S("s48", 10, "speak", ["grandfather"], "Medium close-up", "Tatayya sitting on a wooden bench on the veranda, "
      "speaking to his son off camera, a rudraksha mala in his hand", "wise", lines=[("c10b_line", 0, 4.8)]),
    S("s49", 10, "living", ["raghava"], "Close-up", "present-day Raghava in the hospital corridor, eyes closed, "
      "listening to the memory", "remembering", lines=[("c10b_line", 4.8, 6.8)], motion="his lips part slightly, "
      "a breath", place="10p"),
    S("s50", 10, "speak", ["grandfather"], "Close-up", "Tatayya leaning forward, pressing the rudraksha mala into "
      "his son's hands (only the hands visible)", "wise", lines=[("c10b_line", 6.8, E)]),
    # 11. The chant (three cycles, 34.3 s, over s51–s60 and into scene 12) -----------------------------------------
    S("s51", 11, "speak", ["raghava"], "Close-up", "Raghava in the prayer alcove, eyes fully closed, hands joined, beginning to chant",
      "devout", lines=[("chant", 0, 5.0)]),
    S("s52", 11, "living", ["lakshmi"], "Close-up", "Lakshmi beside him joining the prayer", "chant_join",
      lines=[("chant", 5.0, 8.5)], motion="her lips move softly, tears fall"),
    S("s53", 11, "insert", [], "Close-up", "the Shiva picture as golden sunlight reaches it, the diya flame rising",
      lines=[("chant", 8.5, 11.5)], motion="sunrays sweep across the picture, the flame brightens", refs=["shiva"]),
    S("s54", 11, "speak", ["raghava"], "Medium close-up, low angle", "Raghava chanting with conviction, sunrays across "
      "his face", "devout", lines=[("chant", 11.5, 16.5)]),
    S("s55", 11, "insert", ["abhinav"], "Medium shot inside the theatre", "Abhinav on the operating table under the "
      "lights, still", "unconscious", lines=[("chant", 16.5, 19.5)], state={"abhinav": "ot"},
      motion="the monitor light flickers on his face"),
    S("s56", 11, "hero", ["abhinav"], "Wide shot", "Abhinav's translucent glowing soul in a white kurta rising up out "
      "of his own body, which lies unconscious on the operating table below, into a shaft of soft white-gold light", "eyes_opening", lines=[("chant", 19.5, 24.0)],
      state={"abhinav": "soul"}, motion="the soul lifts upward slowly"),
    # 12. Journey of the soul ---------------------------------------------------------------------------------------
    S("s57", 12, "hero", ["yama1", "yama2"], "Wide low-angle shot", "two tall Yama Dhoothas emerging from the golden "
      "clouds of light, one with a rope noose, one with a mace", "stern", lines=[("chant", 24.0, 29.0)],
      engine="flow-omni", motion="they stride forward out of the light"),
    S("s58", 12, "living", ["abhinav"], "Medium close-up", "Abhinav in soul form looking around the glowing space, "
      "confused", "scared", lines=[("chant", 29.0, E)], state={"abhinav": "soul"}, motion="he turns slowly, "
      "glowing softly"),
    S("s59", 12, "speak", ["yama1"], "Medium close-up", "the first Yama Dhootha holding out the rope noose", "stern",
      lines=[("c12a_line", 0, E)]),
    S("s60", 12, "speak", ["yama2"], "Medium close-up", "the second Yama Dhootha, mace on his shoulder", "stern",
      lines=[("c12b_line", 0, E)]),
    S("s61", 12, "speak", ["abhinav"], "Close-up", "Abhinav in soul form stepping back, pleading", "scared",
      lines=[("c12c_line", 0, E)], state={"abhinav": "soul"}, avoid=NO_SMILE),
    S("s62", 12, "hero", ["shiva"], "Wide shot", "a burst of divine white-gold light opening behind the Yama "
      "Dhoothas as Lord Shiva appears, trishul raised", "divine_command", lines=[("c12d_line", 0, 2.6)],
      engine="flow-omni", motion="the light bursts open, Shiva steps forward"),
    S("s63", 12, "speak", ["shiva"], "Medium close-up", "Lord Shiva speaking with authority, divine light behind him",
      "divine_command", lines=[("c12d_line", 2.6, E)], avoid=NO_SMILE),
    S("s64", 12, "action", ["yama1", "yama2"], "Medium wide shot", "the two Yama Dhoothas bowing low before the light, "
      "the noose lowered", "bowing", lines=[("c12e_line", 0, E)], motion="they bow deeply, then step back into the "
      "light"),
    S("s65", 12, "speak", ["shiva"], "Close-up", "Lord Shiva looking down at Abhinav with compassion",
      "divine_tender", lines=[("c12f_line", 0, E)]),
    S("s66", 12, "hero", ["abhinav"], "Wide shot", "Abhinav's soul drawn gently back down through streams of golden "
      "light towards the hospital below", "eyes_opening", secs=3.0, state={"abhinav": "soul"},
      motion="the soul descends in a stream of light"),
    # 13. The miracle -----------------------------------------------------------------------------------------------
    S("s67", 13, "insert", [], "Close-up", "a patient monitor whose flat line jumps back into a strong green "
      "heartbeat, no readable text", secs=1.5, motion="the line spikes into a steady rhythm"),
    S("s68", 13, "speak", ["doctor"], "Medium close-up", "Dr. Srinivas coming out of the theatre doors towards the "
      "parents off camera, restrained astonishment, looking at them, not at the camera", "astonished_joy", lines=[("c13a_line", 0, 4.8)], state={"doctor": "out"}),
    S("s69", 13, "shock", ["raghava", "lakshmi"], "Medium two-shot", "Raghava and Lakshmi rising from the bench as they "
      "hear it", "stunned_relief", lines=[("c13a_line", 4.8, E)], motion="they rise, hands to their mouths, tears "
      "come", cam="serial triple zoom"),
    S("s70", 13, "speak", ["raghava"], "Close-up", "Raghava looking upward, hands joined", "grateful_tears",
      lines=[("c13b_line", 0, E)]),
    S("s71", 13, "speak", ["lakshmi"], "Close-up", "Lakshmi with her palms pressed together, looking up",
      "joy_tears", lines=[("c13c_line", 0, E)]),
    # 14. Recovery room ---------------------------------------------------------------------------------------------
    S("s72", 14, "establishing", [], "Wide shot", "the bright recovery room with morning sun through the big window, "
      "a plant, steady green monitor lines", secs=2.0, cam="slow push-in"),
    S("s73", 14, "living", ["abhinav"], "Extreme close-up", "Abhinav in the hospital bed opening his eyes",
      "eyes_opening", secs=2.5, state={"abhinav": "bed"}, motion="his eyelids flutter open, a slow blink"),
    S("s74", 14, "speak", ["lakshmi"], "Close-up", "Lakshmi at the bedside holding her son's hand to her cheek",
      "joy_tears", lines=[("c14a_line", 0, 4.6)]),
    S("s75", 14, "living", ["abhinav"], "Close-up", "Abhinav in the bed looking at his mother", "weak_wonder",
      lines=[("c14a_line", 4.6, E)], state={"abhinav": "bed"}, motion="a faint smile, a slow blink"),
    S("s76", 14, "speak", ["raghava"], "Medium close-up", "Raghava standing at the bedside looking down at his son, who "
      "is out of frame below; only Raghava's face is in frame", "proud_smile", lines=[("c14b_line", 0, E)]),
    S("s77", 14, "speak", ["abhinav"], "Close-up", "Abhinav in the bed speaking softly to his parents",
      "weak_wonder", lines=[("c14c_line", 0, 4.6)], state={"abhinav": "bed"}),
    S("s78", 14, "flashback", ["shiva"], "Wide shot, flash memory", "a brief flash memory in the divine white-gold space: the "
      "silhouette of Lord Shiva with his trishul against blinding light, no room, no hospital", "divine_tender", lines=[("c14c_line", 4.6, E)],
      motion="a flash of light, gone", cam="fast flash transition"),
    S("s79", 14, "living", ["abhinav"], "Extreme close-up", "Abhinav in the bed as a voice echoes in his mind",
      "eyes_widen", secs=1.5, state={"abhinav": "bed"}, motion="his eyes widen", avoid=NO_SMILE),
    # 15. Divine voice: the user's Google Flow clip, used whole -----------------------------------------------------
    S("s80", 15, "provided", ["shiva"], "—", "clips/provided/c15a_shiva_flow.mp4, used whole with its own voice",
      lines=[("c15a_line", 0, E)]),
    # 16. End card --------------------------------------------------------------------------------------------------
    S("s81", 16, "endcard", ["shiva", "abhinav"], "Wide poster composition", "Lord Shiva's calm face large and faint "
      "in golden light in the sky, Abhinav below in his light-blue shirt looking up at him, the rudraksha on his "
      "wrist catching the light, empty space at the bottom for the title", "eyes_widen",
      lines=[("c16a_vo", 0, E)], state={"abhinav": "home"}, cam="slow push-in"),
]

CHANT = 34.34  # three cycles with crossfades (edit/assemble.py)


def dur(line: str) -> float:
    if line == "chant":
        return CHANT
    f = next((P / "audio" / "lines").glob(f"{line}_*.wav"))
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(f)], timeout=30))


def segments(v: dict) -> list[tuple[str, float, float]]:
    return [(ln, a, dur(ln) if b is None else b) for ln, a, b in v["lines"]]


def wardrobe(who: str, v: dict) -> str:
    return WARDROBE[who].get(v["state"].get(who, "home"), WARDROBE[who]["home"])


def engine(v: dict) -> str:
    """The engine for the still (user rule, 2026-11-01, after the A/B): emotional faces on Flow Nano Banana Pro,
    which held grief and fear where Kling O3 drifted back to the reference's pleasant face; everything else on Kling
    O3; blood on Qwen. A still that fails review is retaken on Flow Nano Banana Pro whatever its engine."""
    if v["engine"] in ("qwen",):
        return v["engine"]
    if v["expr"] in EMOTIONAL and v["who"]:
        return "flow-nb"
    return "kling-o3"


def clip(v: dict) -> str:
    return "flow-omni" if v["kind"] == "hero" else ""


def references(v: dict) -> list[str]:
    picks = json.loads((P / "shots.json").read_text())["picks"]
    out = []
    for who in v["refs"] or v["who"]:
        if who == "shiva":
            out += ["refs/shiva_default.png", "refs/shiva_full.png"]
        elif who in picks:
            out.append(f"refs/{who}_{picks[who]}.png")
    return out


def prompt(v: dict) -> tuple[str, str]:
    title, mood = SCENE[v["place"] or v["scene"]]
    people = []
    for who in v["who"]:
        w = wardrobe(who, v)
        people.append(f"{IDENTITY[who]}" + (f", {w}" if w else ""))
    # Sample 2026-11-01: with references, Kling O3 kept the reference's pleasant face whenever the expression was
    # stated once mid-prompt (grief came out serene, terror came out smiling at the lens). Ep1's fix was to state
    # the emotion first and last; it is applied to every shot with a face.
    face = f" Expression: {EXPR[v['expr']]}." if v["expr"] else ""
    if v["who"] and v["kind"] not in ("endcard",):
        face += " Nobody looks into the camera lens."
    if v["kind"] in LIPSYNC:
        face += " Three-quarter view towards the camera, both eyes visible, not in profile."
        face += (" One face only in frame (this frame starts a lip-sync shot)." if v["expr"] in OPEN_MOUTH else
                 " The mouth is closed at rest (this frame starts a lip-sync shot); one face only in frame.")
    if v["kind"] == "living":
        face += " The emotion must read fully in the still itself."
    refs = (" Use the reference images only for faces and identity; do not copy their clothing, pose, expression "
            "or background." if v["who"] and "shiva" not in v["who"] else "")
    if v["refs"] and not v["who"]:
        refs += " The framed picture shows Lord Shiva exactly as in the reference images."
    if "shiva" in v["who"]:
        refs += " Lord Shiva must match his reference images exactly: face, hair, costume and ornaments."
    if len(v["who"]) > 1:
        refs += f" Exactly {len(v['who'])} named people; anyone else is a background extra."
    flash = " Warm golden memory glow, softened edges." if v["kind"] == "flashback" else ""
    if not v["who"]:  # review 2026-11-01: Kling drew a face into five empty inserts (s29, s41, s43, s67, s72)
        face += " No people and no faces anywhere in the frame."
    first = last = ""
    if v["expr"]:
        who = " and ".join(IDENTITY[w].split(",")[0].split(" (")[0] for w in v["who"] if w in IDENTITY)
        first = f"{who}: {EXPR[v['expr']]}. "
        last = f" Again: {EXPR[v['expr']]}."
    text = (f"{first}{v['framing']}: {v['action']}.{face} {' '.join(p + '.' for p in people)}{refs} "
            f"Scene: {mood}{flash} {LOOK if v['who'] else LOOK_EMPTY}{last}")
    avoid = AVOID + (f", {v['avoid']}" if v["avoid"] else "")
    return " ".join(text.split()), avoid


def check(vs: list[dict]) -> list[str]:
    errs, seen = [], {}
    for v in vs:
        segs = segments(v)
        if v["kind"] in LIPSYNC:
            if len(segs) != 1:
                errs.append(f"{v['id']}: a lip-sync shot carries exactly one line segment")
            elif segs[0][2] - segs[0][1] > WINDOW + 1e-6:
                errs.append(f"{v['id']}: lip-sync segment {segs[0][2] - segs[0][1]:.2f} s exceeds one window")
            if len([w for w in v["who"] if w != "girl"]) != 1:
                errs.append(f"{v['id']}: a lip-sync shot frames one face")
        if v["kind"] not in NO_FACE_MOTION | LIPSYNC | {"provided", "endcard", "hero"} and not v["motion"] and v["who"]:
            errs.append(f"{v['id']}: a face shot needs subtle motion (no camera-only faces)")
        for ln, a, b in segs:
            seen.setdefault(ln, []).append((a, b, v["id"]))
    lines = sorted(f.name.split("_")[0] + "_" + f.name.split("_")[1] for f in (P / "audio" / "lines").glob("*.wav"))
    for ln in lines + ["chant"]:
        if ln not in seen:
            errs.append(f"{ln}: not placed on any shot")
            continue
        t = 0.0
        for a, b, vid in sorted(seen[ln]):
            if abs(a - t) > 1e-6:
                errs.append(f"{ln}: gap or overlap at {t:.2f} s before {vid}")
            t = b
        if abs(t - dur(ln)) > 0.05:
            errs.append(f"{ln}: covered to {t:.2f} s of {dur(ln):.2f} s")
    return errs


def length(v: dict) -> float:
    s = sum(b - a for _, a, b in segments(v))
    return s if s else (v["secs"] or 2.0)


def main() -> None:
    errs = check(V)
    kinds: dict[str, int] = {}
    for v in V:
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    lip = [v for v in V if v["kind"] in LIPSYNC]
    lip_s = sum(length(v) for v in lip)
    stills = [v for v in V if v["kind"] != "provided" and not v["still"]]
    print(f"{len(V)} visuals, {len(stills)} stills to make; kinds: {kinds}")
    print(f"lip-sync: {len(lip)} shots, {lip_s:.1f} s, {len(lip)} windows (one each)")
    print(f"engines: { {e: sum(engine(v) == e for v in stills) for e in sorted({engine(v) for v in stills})} }")
    print(f"picture time from lines + set lengths: {sum(length(v) for v in V) + 0.5 * len(V):.0f} s (0.5 s gaps)")
    print("checks:", "all pass" if not errs else "\n  " + "\n  ".join(errs))
    if "--check" in sys.argv or errs:
        raise SystemExit(1 if errs else 0)

    OUT.mkdir(exist_ok=True)
    rows = []
    for v in V:
        made = v["kind"] != "provided" and not v["still"]
        text, avoid = prompt(v) if made else ("", "")
        rows.append({**{k: v[k] for k in ("id", "scene", "kind", "who", "expr", "cam", "motion", "still")},
                     "lines": [[ln, round(a, 2), round(b, 2)] for ln, a, b in segments(v)],
                     "seconds": round(length(v), 2),
                     "engine": engine(v) if made else ("reuse" if v["still"] else "provided"),
                     "clip": clip(v),
                     "refs": references(v) if made else [], "prompt": text, "avoid": avoid})
    (OUT / "visuals.json").write_text(json.dumps({"plan": "docs/DRAMA_PIPELINE_V2_2026-11-01.md §2",
                                                  "visuals": rows}, ensure_ascii=False, indent=1))
    spec = json.loads((P / "shots.json").read_text())
    en = {}
    for sh in spec["shots"]:
        ln = sh.get("line") or sh.get("vo")
        if ln:
            en[sh["id"]] = (ln["who"], ln["emotion"], ln["en"])
    md = ["# Mantra Rahasyam Ep1 — v2 image prompts (serial grammar)", "",
          "Generated by `edit/v2_prompts.py` from the reused audio stage. Plan: "
          "`docs/DRAMA_PIPELINE_V2_2026-11-01.md` §2. Still engines (user rule after the A/B, 2026-11-01): "
          "`flow-nb` = Google Flow Nano Banana Pro (free) for emotional faces; `kling-o3` (PoYo) for everything else; "
          "`qwen` (PoYo) for blood. Every still is reviewed; one that fails is retaken on `flow-nb`. Hero shots also "
          "get a Flow Omni clip from their still.", "",
          f"**{len(V)} visuals, {len(stills)} stills**, {len(lip)} lip-sync shots ({lip_s:.0f} s, one window each). "
          "Shiva's last line is the user's Flow clip, used whole.", "",
          "**Lip-sync shots at risk of InfiniteTalk's grin** (fear/grief faces; OmniHuman is the per-shot fallback): "
          + ", ".join(v["id"] for v in V if v["kind"] in LIPSYNC and v["expr"] in GRIEF_RISK) + ".", "",
          "**Rule exceptions:** prayers (s28, s37, s38) are heard over closed-mouth living stills; Lakshmi's plea "
          "c07d plays over the signing insert and her sobbing reaction (s34–s35).", ""]
    for sc in sorted({v["scene"] for v in V}):
        md += [f"## {sc}. {SCENE[sc][0]}", "", f"*Mood:* {SCENE[sc][1]}", ""]
        for r in (r for r in rows if r["scene"] == sc):
            md.append(f"### {r['id']} · {r['kind']} · {', '.join(r['who']) or 'no people'} · {r['seconds']} s · "
                      f"{r['engine']}")
            for ln, a, b in r["lines"]:
                key = ln.split("_")[0]
                who, emo, text = en.get(key, ("raghava", "devoted", "Mahamrityunjaya chant")) if ln != "chant" \
                    else ("raghava", "devoted", "Mahamrityunjaya chant")
                md.append(f"- 🔊 `{ln}` {a:.1f}–{b:.1f} s · {who} ({emo}): *{text}*")
            if r["kind"] == "provided":
                md += ["- Reused as-is; no still.", ""]
                continue
            if r["still"]:
                md += [f"- Reuses the still of {r['still']}; motion: {r['motion'] or '—'}; camera: {r['cam'] or 'static'}.", ""]
                continue
            if r["refs"]:
                md.append(f"- Refs: {', '.join('`' + x + '`' for x in r['refs'])}")
            if r["motion"] or r["cam"]:
                md.append(f"- Motion: {r['motion'] or '—'}; camera: {r['cam'] or 'static'}")
            md += ["", "```text", r["prompt"], "", f"Avoid: {r['avoid']}", "```", ""]
    (OUT / "IMAGE_PROMPTS.md").write_text("\n".join(md))
    print(f"wrote {OUT / 'visuals.json'} and {OUT / 'IMAGE_PROMPTS.md'}")


if __name__ == "__main__":
    main()
