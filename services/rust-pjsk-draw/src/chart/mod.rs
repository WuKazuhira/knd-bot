mod canvas;
mod score;

pub use canvas::{
    ChartMeta, CoverRect, CoverStyle, CoverText, MusicMeta, SusCanvas, SusWithSkillCanvas,
    legacy_sus_svg, rasterize,
};
pub use score::{
    Directional, Event, Note, Score, ScoreNote, Slide, Tap, get_denominator, parse_notes,
};

#[derive(Clone, Debug, PartialEq)]
pub struct Word {
    pub bar: f64,
    pub text: String,
}

pub fn load_lyric(input: &str) -> Vec<Word> {
    let mut words = Vec::new();
    for line in input.lines() {
        let Some((bar, text)) = line.trim().split_once(": ") else {
            continue;
        };
        let Ok(bar) = bar.parse::<u32>() else {
            continue;
        };
        let count = text.split('/').count() as f64;
        words.extend(
            text.split('/')
                .enumerate()
                .filter(|(_, text)| !text.is_empty())
                .map(|(index, text)| Word {
                    bar: f64::from(bar) + index as f64 / count,
                    text: text.to_owned(),
                }),
        );
    }
    words
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gfx::FontRegistry;
    use image::{Rgba, RgbaImage};

    const SUS: &str = "
#00002: 4
#BPM01: 120
#BPM02: 180
#00008: 01
#00108: 0002
#00202: 3
#00012: 22
#000320: 12
#000340: 0032
#00014: 0012
#001360: 0022
#00156: 0032
#00010: 0011
#00111: 21
";

    #[test]
    fn parses_timing_slide_links_and_markers_and_rebases() {
        let score = Score::parse(SUS).unwrap();
        assert_eq!(score.get_time(1.5), 3.);
        assert_eq!(score.get_event(2.).bpm, Some(180.));
        assert_eq!(score.get_event(2.).bar_length, Some(3.));
        for bar in [-0.5, 0., 0.75, 1.5, 2., 4.] {
            assert!((score.get_bar(score.get_time(bar)) - bar).abs() < 1e-10);
        }
        let notes = score.notes();
        let ScoreNote::Slide(head) = notes[0] else {
            panic!()
        };
        assert_eq!(head.tap.unwrap().0.kind, 2);
        assert_eq!(notes[head.next.unwrap()].note().kind, 0);
        let ScoreNote::Slide(among) = notes.iter().find(|note| note.note().kind == 3).unwrap()
        else {
            panic!()
        };
        assert!(among.is_among_note() && !among.is_path_note());
        let ScoreNote::Slide(end) = notes.last().unwrap() else {
            panic!()
        };
        assert_eq!(end.head, Some(0));
        assert_eq!(end.directional.unwrap().note.kind, 3);
        assert_eq!(score.get_event(0.5).text.as_deref(), Some("SKILL"));
        assert_eq!(score.get_event(1.).text.as_deref(), Some("SUPER FEVER!!"));

        let target = score.rebase(
            vec![Event {
                bpm: Some(60.),
                ..Event::default()
            }],
            1.5,
        );
        assert_eq!(target.notes().last().unwrap().note().bar, 0.375);
        assert_eq!(target.events()[0].bar, -0.125);
        assert_eq!(target.get_time(0.), 0.);
        assert_eq!(target.get_time(-0.125), -0.5);
        assert_eq!(get_denominator(3. / 16.), 16);
        assert_eq!(get_denominator(1. / 7.), 7);
        assert_eq!(load_lyric("4: one//three\ncomment")[1].bar, 4. + 2. / 3.);
        assert!(Score::parse("#00008: 01").is_err());
        assert!(Score::parse("#00012: 1").is_err());
    }

    #[test]
    fn renders_embedded_sprites_and_skill_overlays() {
        let dir = tempfile::tempdir().unwrap();
        let sprite = RgbaImage::from_pixel(12, 12, Rgba([255, 0, 0, 255]));
        for name in [
            "notes_crtcl_left.png",
            "notes_crtcl_middle.png",
            "notes_crtcl_right.png",
            "notes_long_among_crtcl.png",
            "notes_flick_arrow_crtcl_02_diagonal_left.png",
            "notes_normal_left.png",
            "notes_normal_middle.png",
            "notes_normal_right.png",
            "jacket.png",
        ] {
            sprite.save(dir.path().join(name)).unwrap();
        }
        let score = Score::parse(SUS).unwrap();
        let meta = ChartMeta {
            title: "a&b".into(),
            artist: "Artist".into(),
            difficulty: "master".into(),
            play_level: Some(30),
        };
        let jacket = dir.path().join("jacket.png");
        let canvas = SusCanvas {
            score: &score,
            meta: &meta,
            notes_dir: dir.path(),
            jacket: Some(&jacket),
            note_size: 1.,
            words: &[],
        };
        let fonts = FontRegistry::new(dir.path()).unwrap();
        let svg = canvas.to_svg().unwrap();
        assert!(svg.contains("a&amp;b"));
        assert!(svg.contains("fill=\"#fcf1c3\" fill-opacity=\"0.8\""));
        assert!(svg.contains("data:image/png;base64,"));
        let image = rasterize(&svg, &fonts, 1.).unwrap();
        assert_eq!(image.dimensions(), (288, 1216));
        assert_eq!(image.get_pixel(80, 500).0, [68, 68, 68]);
        assert!(
            image
                .enumerate_pixels()
                .any(|(_, y, p)| y < 860 && p.0 == [255, 0, 0])
        );
        assert_eq!(
            rasterize(&canvas.scale_svg().unwrap(), &fonts, 1.)
                .unwrap()
                .dimensions(),
            (120, 120)
        );

        let svg = SusWithSkillCanvas {
            canvas,
            music_meta: Some(&MusicMeta {
                fever_end_time: Some(3.),
                fever_score: Some(0.03),
                skill_score_solo: vec![0.1],
                skill_score_multi: vec![0.2],
            }),
            display_skill_extra: true,
        }
        .to_svg()
        .unwrap();
        assert!(svg.contains("単+10.00% 多+20.00%"));
        assert!(svg.contains("多+3.00%"));
        let image = rasterize(&svg, &fonts, 1.).unwrap();
        assert_eq!(image.get_pixel(80, 500).0, [136, 136, 136]);
    }
}
