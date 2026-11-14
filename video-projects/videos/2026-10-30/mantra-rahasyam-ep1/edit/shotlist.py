"""Images stage, pass B plan: the visual shots of the episode, written into shots.json["visuals"].

usage: shotlist.py
Each visual: id, scene, kind (lipsync = one speaking face, InfiniteTalk 720p; motion = Wan 2.2 with any lines played
over it), who (characters in frame, for reference images), lines (dialogue ids heard over it), image (prompt).
Every lip-sync shot frames a single face, so no shot needs OmniHuman. Masked operating-theatre lines and short
off-camera lines play over motion shots. The chant is one lip-synced cycle shown twice, with cut-aways under the
middle cycle.
"""

import json
from pathlib import Path

P = Path(__file__).resolve().parents[1]

V = [
    # 1. Home, morning
    ("v01", 1, "motion", [], ["c01a_vo"], "Wide exterior of a small, cheerful two-storey Hyderabad house at sunrise, "
     "potted tulsi plant and rangoli at the gate, warm golden morning light, birds, clear blue sky."),
    ("v02", 1, "lipsync", ["lakshmi"], ["c01b_line"], "Lakshmi in a sunny kitchen doorway holding a small steel bowl "
     "of curd and sugar and a spoon, smiling lovingly at someone just off camera (Abhinav's shoulder blurred in the "
     "foreground, back to camera). Medium close-up on her face."),
    ("v03", 1, "lipsync", ["abhinav"], ["c01c_line"], "Abhinav in a bright living room, office bag on his shoulder, "
     "warm affectionate smile, speaking to his mother off camera. Medium close-up."),
    ("v04", 1, "lipsync", ["raghava"], ["c01d_line"], "Raghava sitting on a cane chair in a sunlit living room with a "
     "folded Telugu newspaper, looking up with a caring smile, pointing gently at his own wrist. Medium close-up."),
    ("v05", 1, "lipsync", ["abhinav"], ["c01e_line"], "Abhinav at the open front door holding a black open-face "
     "helmet, turning back with a cheerful wave, bright morning light behind him. Medium shot, face clearly visible."),
    # 2. Accident
    ("v06", 2, "motion", ["abhinav"], ["c02a_vo"], "Abhinav riding a red motorbike with his helmet on along a sunny, "
     "busy Hyderabad road, trees and shops, smiling, seen from the front side. Wide tracking shot."),
    ("v07", 2, "motion", [], [], "A little girl of about five in a yellow frock runs onto the sunny road chasing a "
     "red ball, cars and a lorry approaching in the distance. Wide street-level shot."),
    ("v08", 2, "motion", ["abhinav"], [], "Abhinav swerving his red motorbike sharply to avoid the little girl in the "
     "yellow frock, a large lorry looming behind, motion blur, tense but no impact shown. Wide shot."),
    ("v08b", 2, "motion", ["abhinav"], [], "(see FIXES)"),
    ("v09", 2, "motion", ["abhinav"], [], "Aftermath on a sunny road: a red motorbike lying on its side, a young man lying "
     "still on the road seen from a distance, people rushing towards him, the little girl safe in her mother's arms "
     "at the roadside. No blood, no gore. Wide shot."),
    # 3. Passerby
    ("v10", 3, "lipsync", ["passerby"], ["c03a_line"], "The auto driver beside his yellow-and-green auto-rickshaw on "
     "the sunny road, shocked, hand on his head, looking towards the accident. Medium close-up."),
    ("v11", 3, "lipsync", ["passerby"], ["c03b_line"], "The auto driver kneeling at the roadside, phone to his ear, "
     "urgent and panicked, people gathering behind him. Medium close-up."),
    # 4. Ambulance
    ("v12", 4, "motion", [], ["c04a_vo"], "A white 108 ambulance with flashing lights racing through daytime "
     "Hyderabad traffic, cars moving aside. Wide shot."),
    ("v13", 4, "motion", [], [], "Paramedics wheeling a stretcher through the bright glass entrance of a modern "
     "hospital emergency ward, nurses rushing to help, clean white and pastel interior. Wide shot."),
    # 5. Phone call
    ("v14", 5, "lipsync", ["nurse1"], ["c05a_line"], "Nurse Swathi at a bright, clean hospital nurses' station, "
     "holding a desk phone to her ear, gentle and professional. Medium close-up."),
    ("v15", 5, "lipsync", ["lakshmi"], ["c05b_line"], "Lakshmi in her sunny kitchen holding a mobile phone to her "
     "ear, worried expression. Medium close-up."),
    ("v16", 5, "lipsync", ["nurse1"], ["c05c_line"], "Nurse Swathi on the desk phone, a serious, careful "
     "expression, slightly closer framing. Close-up."),
    ("v17", 5, "lipsync", ["lakshmi"], ["c05d_line"], "Lakshmi in the kitchen, a steel glass falling from her hand, "
     "shocked and crying out, phone still in her other hand. Medium close-up."),
    # 6. Rushing
    ("v18", 6, "motion", ["raghava", "lakshmi"], ["c06a_line"], "Raghava riding a grey scooter through daytime "
     "traffic with Lakshmi sitting behind him, her hands folded in prayer, tears on her cheeks, both worried. "
     "Medium tracking shot."),
    # 7. Doctor and consent
    ("v19", 7, "lipsync", ["doctor"], ["c07a_line"], "Dr. Srinivas in a bright, clean hospital corridor with "
     "pastel walls and plants, explaining gravely to someone off camera. Medium close-up."),
    ("v20", 7, "lipsync", ["raghava"], ["c07b_line"], "Raghava in the bright hospital corridor, trembling, "
     "desperate, asking the doctor off camera. Medium close-up."),
    ("v21", 7, "lipsync", ["doctor"], ["c07c_line"], "Dr. Srinivas holding out a clipboard with a consent form and "
     "a pen, gentle but firm. Medium close-up."),
    ("v22", 7, "lipsync", ["lakshmi"], ["c07d_line"], "Lakshmi in the hospital corridor, crying and pleading with "
     "the doctor off camera, hands joined. Medium close-up."),
    ("v23", 7, "motion", ["raghava"], [], "Close-up of Raghava's trembling hand signing a hospital consent form on a "
     "clipboard, his rudraksha mala and wristwatch visible, bright light."),
    # 8. Prayer and tears
    ("v24", 8, "lipsync", ["lakshmi"], ["c08a_line"], "Lakshmi at a small bright hospital prayer corner with a "
     "framed picture of Lord Shiva and a lit oil lamp, sobbing and praying with joined hands. Medium close-up."),
    ("v25", 8, "lipsync", ["raghava"], ["c08b_line"], "Raghava at the hospital prayer corner, broken and pleading, "
     "looking up towards the Shiva picture. Medium close-up."),
    ("v26", 8, "lipsync", ["lakshmi"], ["c08c_line"], "Lakshmi sitting on a hospital corridor bench, quiet and "
     "heartbroken, staring ahead, a tear rolling down. Close-up."),
    # 9. Operation critical
    ("v27", 9, "motion", ["nurse1"], [], "Bright hospital corridor outside the operating theatre, a red light glowing "
     "above the double doors, Nurse Swathi rushing out and another nurse rushing in. Wide shot."),
    ("v28", 9, "motion", ["doctor", "nurse2"], ["c09a_line", "c09b_line"], "Inside a bright, clean operating "
     "theatre: Dr. Srinivas and Nurse Rani in surgical masks and caps working urgently, a monitor showing a falling "
     "heart line, a defibrillator being readied. Medium-wide shot."),
    ("v29", 9, "motion", ["raghava", "lakshmi"], [], "Raghava and Lakshmi standing in the bright corridor outside "
     "the operating theatre, terrified, holding each other's hands. Medium shot."),
    # 10. Father remembers
    ("v30", 10, "motion", ["raghava"], ["c10a_vo"], "Raghava in the hospital corridor slowly closing his eyes, a soft "
     "warm golden glow beginning around him, memory coming back. Close-up."),
    ("v31", 10, "lipsync", ["grandfather"], ["c10b_line"], "Flashback: Tatayya sitting on the step of a sunlit "
     "traditional Telugu home courtyard with a tulsi plant, speaking wisely and gently to someone off camera, warm "
     "golden light. Medium close-up."),
    # 11. Chant (one lip-synced cycle, shown first and third; cut-aways under the second)
    ("v32", 11, "lipsync", ["raghava"], ["c11a_line"], "Raghava in the hospital corridor, eyes closed, palms joined "
     "holding his rudraksha mala, chanting with deep devotion, soft golden light on his face. Medium close-up."),
    ("v33", 11, "motion", ["lakshmi"], [], "Lakshmi beside him with folded hands and closed eyes, joining the prayer, "
     "soft golden light. Medium close-up."),
    ("v34", 11, "motion", [], [], "Soft golden light and faint sacred sound waves flowing down the bright hospital "
     "corridor and through the operating theatre doors. Wide shot."),
    # 12. Journey of the soul
    ("v35", 12, "motion", ["abhinav"], [], "Abhinav's soul, glowing softly in a plain white kurta, rising gently from "
     "the operating table, translucent, the surgical team below. Bright white light. Wide shot."),
    ("v36", 12, "motion", ["yama1", "yama2"], [], "Two tall Yama Dhoothas in black-and-gold armour emerging from a "
     "soft silver mist, one holding a rope noose, stern and dignified, in a bright otherworldly space. Wide shot."),
    ("v37", 12, "lipsync", ["yama1"], ["c12a_line"], "Yama Dhootha 1 in the silver mist, holding the rope noose, "
     "speaking sternly. Medium close-up."),
    ("v38", 12, "lipsync", ["yama2"], ["c12b_line"], "Yama Dhootha 2 in the silver mist, holding a mace, speaking "
     "with command. Medium close-up."),
    ("v39", 12, "lipsync", ["abhinav"], ["c12c_line"], "Abhinav's soul in a plain white kurta in the bright mist, "
     "scared and pleading, soft glow around him. Medium close-up."),
    ("v40", 12, "motion", ["shiva"], [], "A brilliant white-gold light opens in the sky and Lord Shiva appears within "
     "it, trishul in hand, the mist parting. Wide shot."),
    ("v41", 12, "lipsync", ["shiva"], ["c12d_line"], "Lord Shiva in radiant white-gold light, commanding and "
     "authoritative, looking down. Medium close-up."),
    ("v42", 12, "motion", ["yama1", "yama2"], ["c12e_line"], "The two Yama Dhoothas bowing their heads respectfully "
     "and stepping back into the mist. Medium-wide shot."),
    ("v43", 12, "motion", ["shiva", "abhinav"], ["c12f_line"], "Lord Shiva raising his right hand in blessing as "
     "Abhinav's soul glides gently back down towards a glowing hospital below. Wide shot."),
    # 13. Miracle
    ("v44", 13, "motion", ["doctor", "nurse2"], [], "In the bright operating theatre a monitor's heart line jumps "
     "back to a strong steady rhythm; Dr. Srinivas and Nurse Rani look at each other in astonishment. Medium shot."),
    ("v45", 13, "lipsync", ["doctor"], ["c13a_line"], "Dr. Srinivas in the bright corridor, surgical cap pulled "
     "down, astonished and joyful, talking to the parents off camera. Medium close-up."),
    ("v46", 13, "lipsync", ["raghava"], ["c13b_line"], "Raghava in tears of gratitude, looking upward, hands joined. "
     "Close-up."),
    ("v47", 13, "lipsync", ["lakshmi"], ["c13c_line"], "Lakshmi crying with joy, hands joined, looking upward. "
     "Close-up."),
    # 14. Recovery room
    ("v48", 14, "motion", ["raghava", "lakshmi"], [], "Raghava and Lakshmi entering a bright, sunny recovery room "
     "with green steady monitors, Abhinav asleep in bed with a head bandage. Wide shot."),
    ("v49", 14, "motion", ["abhinav"], [], "Abhinav in the hospital bed with a head bandage slowly opening his eyes "
     "(normal eyes, no glow), soft daylight on his face. Close-up."),
    ("v50", 14, "lipsync", ["lakshmi"], ["c14a_line"], "Lakshmi at the bedside, holding Abhinav's hand, tears of "
     "relief and joy. Medium close-up on her face."),
    ("v51", 14, "lipsync", ["raghava"], ["c14b_line"], "Raghava standing at the bedside, proud and emotional, hand "
     "on his son's shoulder. Medium close-up on his face."),
    ("v52", 14, "lipsync", ["abhinav"], ["c14c_line"], "Abhinav in the hospital bed with a head bandage, weak "
     "smile, wondering, looking at his parents off camera, rudraksha on his right wrist. Medium close-up."),
    # 15. Divine voice
    ("v53", 15, "motion", ["abhinav"], ["c15a_line"], "Abhinav in the hospital bed, his eyes widening as he "
     "remembers, a faint golden light reflected in his eyes and on his face. Close-up."),
    # 16. End card
    ("v54", 16, "motion", ["shiva"], ["c16a_vo"], "A silhouette of Lord Shiva in soft white-gold light high above a "
     "sunlit Hyderabad skyline, peaceful and majestic, space at the bottom for a title. Wide shot."),
]


FIXES = {
 # Grief beats in the phone call: "gentle"/"professional" rendered as a smile, so the framing words are neutral now.
 "v14": "Nurse Swathi at a bright, clean hospital nurses' station, holding a desk phone to her ear, tense and grave "
        "while delivering bad news. Medium close-up.",
 "v16": "Nurse Swathi holding a black desk-phone receiver to her ear and speaking gravely into it, a grave, "
        "apologetic expression, slightly closer framing. Close-up.",
 "v18": "Raghava riding a grey scooter through a quiet daytime street with Lakshmi sitting behind him, both tense "
        "and worried, only two people in the frame. Medium tracking shot.",
 "v33": "Lakshmi standing alone in the bright hospital corridor with folded hands and closed eyes, praying with deep devotion, soft golden light on her face. Medium close-up. Only Lakshmi in frame.",
 "v40": "High in a bright blue sky above soft white clouds, a brilliant white-gold light opens and Lord Shiva appears within it, trishul in hand, floating among the clouds. No street, no buildings, no ground. Wide shot.",
 "v42": "The two Yama Dhoothas bowing their heads respectfully and stepping back into a soft silver otherworldly mist, pale blue and silver light all around. No street, no buildings, no city. Medium-wide shot.",
 "v49": "Abhinav lying back on white pillows in a hospital bed, wearing a loose collarless pale-blue patient gown with short sleeves (a hospital gown, not a shirt), a white bandage around his head, an IV line in his left hand, slowly opening his eyes (normal eyes, no glow), soft daylight on his face. Close-up.",
 "v52": "Abhinav lying back on white pillows in a hospital bed, wearing a loose collarless pale-blue patient gown with short sleeves (a hospital gown, not a shirt), a white bandage around his head, weak smile, wondering, looking at his parents off camera, rudraksha on his right wrist. Medium close-up.",
 "v08": "The moment before impact on a busy, cluttered Hyderabad road in hard daylight: honking traffic, autos, buses and two-wheelers, dust, potholes and broken tarmac, roadside stalls and parked bikes; not an empty or clean road. Abhinav on his red motorbike swerves hard to avoid a little girl of about five in a yellow frock who has run into the road after a red ball; a large Indian lorry is right behind him, brakes screeching, motion blur, dust kicking up. Dramatic low wide shot.",
 "v08b": "Split second after the crash on a busy, cluttered Hyderabad road in hard daylight: honking traffic, autos, buses and two-wheelers, dust, potholes and broken tarmac, roadside stalls and parked bikes; not an empty or clean road: the red motorbike skidding on its side throwing sparks, Abhinav thrown onto the tarmac, his helmet rolling away, the lorry stopped at an angle behind, bystanders turning in shock. Gritty, realistic, motion blur. Wide shot.",
 "v09": "Aftermath on a busy, cluttered Hyderabad road in hard daylight with stopped autos, buses and two-wheelers, dust and broken tarmac: Abhinav lying motionless and unconscious on the road, eyes closed, face slack and pale, no expression, in his light-blue shirt, blood splattered across the tarmac beside him and a pool of blood spreading from his head, his cracked helmet and the damaged red motorbike nearby, broken mirror glass scattered; people rushing in and gathering around in shock; the little girl in her YELLOW frock safe and crying in her mother's arms at the roadside. Realistic accident scene. Wide shot.",
 "v13": "Paramedics urgently wheeling a stretcher with an injured young man (head bandaged, blood on his shirt) through the bright glass entrance of a modern hospital emergency ward, nurses running to help; everyone tense, worried and focused, serious faces, no smiles; motion and urgency. Clean white and pastel interior. Wide shot.",
 "v43": "Lord Shiva high above the clouds in a radiant burst of white-gold light, raising his right hand in blessing (abhaya mudra), looking down with compassion; far below him, Abhinav's small glowing soul in a white kurta, seen from behind, glides gently down through the clouds on a beam of light towards a bright hospital building on the ground. Majestic sense of scale, divine and peaceful. Wide shot."
}  # redo prompts, 2026-10-31


def main() -> None:
    spec = json.loads((P / "shots.json").read_text())
    known = {f"{s['id']}_{'vo' if 'vo' in s else 'line'}" for s in spec["shots"]}
    used = [line for v in V for line in v[4]]
    assert set(used) == known and len(used) == len(known), (sorted(known - set(used)), sorted(set(used) - known))
    spec["visuals"] = [{"id": i, "scene": sc, "kind": k, "who": who, "lines": lines, "image": img}
                       for i, sc, k, who, lines, img in [(a, b, c, d, e, FIXES.get(a, f)) for a, b, c, d, e, f in V]]
    (P / "shots.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n")
    lips = [v for v in V if v[2] == "lipsync"]
    print(f"{len(V)} visuals: {len(lips)} lip-sync (one face each), {len(V) - len(lips)} motion")


if __name__ == "__main__":
    main()
