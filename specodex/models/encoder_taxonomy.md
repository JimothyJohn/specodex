# Encoder Taxonomy Reference

Source-of-truth research for the closed enums that back
`specodex/models/encoder.py`. Cited from vendor docs and standards;
update when you widen an enum or change a default.

Originally `todo/DOUBLE_TAP_encoder_taxonomy.md` — moved next to the
code that uses it after the DOUBLE_TAP plan-doc was retired (its
scope shipped end-to-end via PR #91).

Goal: replace `Optional[str | List[str]]` with a closed taxonomy that
lets us match motors to drives across vendors. Two orthogonal axes:
**device** (the physical sensor) and **protocol** (the wire format).
A motor declares one of each; a drive declares a list of supported
protocols.

## 1. Feedback DEVICE types

Closed enum (`EncoderDevice`):

| Value | What it physically measures | Resolution units | Pos / Vel | Vendor synonyms |
|---|---|---|---|---|
| `incremental_optical` | Light through grated disk → quadrature pulses | PPR, lines/rev, CPR (=4×PPR after edge-quad) | Pos+Vel (derived) | "incremental encoder", "TTL encoder", "line driver", "quadrature encoder" |
| `absolute_optical` | Light through coded (Gray) disk | bits/turn | Pos | "absolute encoder", "single-turn absolute" |
| `absolute_optical_multiturn` | Same + geared/battery counter for revs | bits/turn + bits/rev | Pos | "multi-turn absolute", "MT", "multiturn" |
| `incremental_magnetic` | Hall-array reading magnet ring | PPR | Pos+Vel | "magnetic incremental", "MR encoder" |
| `absolute_magnetic` | Magneto-resistive sensing of coded magnet pattern | bits/turn | Pos | "magnetic absolute" |
| `sin_cos_analog` | Analog sine/cosine pair (1 Vpp or 11 µAss) | lines/rev (interpolated) | Pos+Vel | "sin/cos", "1Vpp", "Heidenhain analog" |
| `resolver` | Rotary transformer; sin/cos windings excited at carrier | Pole-pair count (1X, 2X, 4X…) + carrier kHz | Pos+Vel | "resolver", "rotary transformer", "RVDT" |
| `inductive` | Inductive coupling coils on coded scale (no glass) | bits/turn | Pos | "inductive encoder" — Renishaw ASTRiA, AMO LIA-20 |
| `capacitive` | Capacitive coupling between coded patterns | counts/rev | Pos | "capacitive encoder" — Netzer, CUI AMT |
| `tachometer_dc` | Brushed DC generator output proportional to RPM | V/krpm | Vel only | "tachogenerator", "tach" |
| `hall_only` | UVW Hall switches (commutation, not position) | 6 sectors / electrical rev | Pos (coarse, commutation only) | "Hall sensors", "UVW commutation" |
| `none` | No feedback (V/Hz, sensorless FOC, open-loop steppers) | — | — | "sensorless", "open loop" |
| `unknown` | Sentinel — LLM punted, verifier picks up | — | — | — |

Notes:
- "Resolver" without pole-pair count almost always means **1X**
  (single-pair) on industrial servo motors. 2X/4X exist on large
  frames and high-precision applications. Source: Parker MB-series
  resolver-feedback motor docs.
- "Optical absolute" can be single-turn or multi-turn — keep them as
  separate enum values because the multi-turn distinction matters
  for absolute-positioning systems and that data IS in the spec sheet.

## 2. Feedback PROTOCOLS (wire format)

Closed enum (`EncoderProtocol`):

| Value | Vendor / origin | Inc/Abs | ST/MT | Typical resolution | Notes |
|---|---|---|---|---|---|
| `quadrature_ttl` | Generic | Inc | ST only | 500–10,000 PPR | RS-422 line driver, A/B/Z. Dominant cheap incremental wire format. |
| `open_collector` | Generic | Inc | ST only | 100–2,500 PPR | Cheap industrial — non-differential. |
| `hall_uvw` | Generic | Pos (commutation) | — | 6 sectors | Three open-collector lines for BLDC commutation. |
| `sin_cos_1vpp` | Heidenhain (de facto) | Inc (interpolated) | ST only | 2,048 lines/rev | 1 Vpp differential analog sine/cosine. |
| `ssi` | Generic, IC-Haus 1980s | Abs | ST + MT | 12–25 bits | Synchronous Serial Interface, unidirectional. |
| `biss_c` | iC-Haus, open | Abs (or Inc) | ST + MT | 18–32 bits | Open protocol, bidirectional, ≤10 MHz clock. Used by Renishaw, Hengstler, AMO, Kübler. |
| `endat_2_1` | Heidenhain (1995) | Abs | ST + MT | 13–25 bits | Half-digital, half-analog (sin/cos for fine interpolation). |
| `endat_2_2` | Heidenhain (2007) | Abs | ST + MT | up to 47-bit position word | Pure-serial, SIL-3 capable. |
| `hiperface` | Sick Stegmann (1996) | Abs | ST + MT | 12–18 bits position + sin/cos | 8-wire: 4 sin/cos + 2 RS-485 + 2 power. |
| `hiperface_dsl` | Sick (2011) | Abs | ST + MT | 18–24 bits | All-digital, 2 wires, runs in motor power cable (One Cable). **Electrically incompatible with Hiperface.** |
| `tamagawa_t_format` | Tamagawa Seiki | Abs | ST + MT | 17 or 23 bit/turn + 16 bit MT | RS-485, 2.5 Mbps. Used by Nidec, Sanyo Denki, many JP OEMs. |
| `mitsubishi_j5` | Mitsubishi proprietary | Abs (and Inc variant) | ST + MT (batteryless) | 26-bit (67,108,864 PPR) | HK-KT/HK-ST motors on MR-J5 drives. |
| `mitsubishi_j4` | Mitsubishi proprietary | Abs | ST + MT | 22-bit (4,194,304 PPR) | HG motors on MR-J4 drives; **not** wire-compatible with J5. |
| `mitsubishi_j3` | Mitsubishi proprietary | Abs | ST + MT | 18-bit | HF/HC motors on MR-J3 drives. |
| `panasonic_a6` | Panasonic proprietary | Abs (and Inc) | ST + MT (batteryless) | 23-bit (8,388,608 PPR) | MINAS A6 motors. |
| `yaskawa_sigma` | Yaskawa proprietary | Abs (and Inc) | ST + MT | 24-bit (Sigma-7) | Sigma-V is also 20/24-bit; not wire-compatible across generations. Single enum because public docs don't distinguish, but flag in `raw` if specific generation matters. |
| `fanuc_serial` | FANUC proprietary | Abs (battery) | ST + MT | up to ~32M counts | RS-422-based. Two sub-variants ("legacy 4-wire" and "high-speed 2-wire") that aren't wire-compatible. |
| `drive_cliq` | Siemens proprietary | Abs | ST + MT | 22-bit ST + 12-bit MT (S-1FK2) | Siemens-only. |
| `oct_beckhoff` | Beckhoff (2011) | Abs | ST + MT | 18–24 bits | Beckhoff One Cable Technology. Internally Hiperface-DSL-class but Beckhoff brands separately — keep distinct. |
| `resolver_analog` | Generic | "Abs" (within one electrical rev) | ST (within pole-pair) | 12–16 bit RDC | Raw sin/cos — drive does R-to-D conversion. |
| `proprietary_other` | — | — | — | — | Catch-all for Sankyo Seiki, Lenze, Kollmorgen smart-feedback, etc. |
| `unknown` | Sentinel — LLM punted, verifier picks up | — | — | — | — |

## 3. Disambiguation rules (LLM defaults when spec is silent)

Encoded as docstrings on the validator + injected into the system
prompt so Gemini sees them in `response_schema`:

1. **"Incremental N ppr" with no protocol named** → `device=incremental_optical`,
   `protocol=quadrature_ttl`. Quadrature TTL/RS-422 is the de facto
   industrial incremental wire standard; open-collector is only on
   hobby-grade gear.
2. **"N-bit absolute" with no protocol named** → unknown; do NOT guess.
   Set `protocol=null`, `bits_per_turn=N`. Cross-vendor absolute bit
   counts collide (Mitsubishi 26-bit vs Yaskawa 24-bit vs Heidenhain
   ECN-1325 25-bit), so guessing the protocol from bit count alone
   is fabrication.
3. **"Multi-turn absolute" with no rev count** → set `multiturn=true`,
   `multiturn_bits=null`. Don't infer the bit count. Industry default
   is 12 bits (4,096 revs) but it's vendor-specific.
4. **"Resolver" with no pole-pair count** → assume **1X** (1 pole pair).
   Most servo motors and matched resolvers ship 1X.
5. **"EnDat" with no version → `endat_2_2`**. EnDat 2.2 was released
   2007 and is the dominant Heidenhain protocol shipped with new
   motors since ~2015. EnDat 2.1 stays in legacy retrofits.
6. **"Hiperface" with no "DSL" → classic `hiperface`** (8-wire). Hiperface
   DSL is always called out explicitly because the cable architecture
   is fundamentally different (two wires, runs over motor power cable).
7. **"Smart Abs" or "T-format"** → `tamagawa_t_format`. Default to 17-bit
   ST + 16-bit MT unless otherwise stated; 23-bit is a higher-end
   variant.
8. **"Batteryless multi-turn"** is a Wiegand-wire / energy-harvesting
   feature — record as `multiturn_battery_backed=false` flag, not as
   a separate protocol. (Panasonic A6 and Mitsubishi MR-J5 both ship
   batteryless.)

## 4. Brand → protocol shortcuts (~2020–2026)

`(vendor, series) → (default_device, default_protocol, default_resolution)`.
Use as fallback when the spec sheet is being LLM-extracted and the
encoder line is missing.

| Vendor / series | Device | Protocol | Resolution |
|---|---|---|---|
| Mitsubishi MR-J5 + HK-KT/HK-ST | absolute_optical_multiturn | mitsubishi_j5 | 26-bit ST, batteryless MT |
| Mitsubishi MR-J4 + HG | absolute_optical_multiturn | mitsubishi_j4 | 22-bit ST + 16-bit MT |
| Yaskawa Sigma-7 (SGM7A/J/G) | absolute_optical_multiturn | yaskawa_sigma | 24-bit ST + 16-bit MT (batteryless) |
| Yaskawa Sigma-V | absolute_optical_multiturn | yaskawa_sigma | 20-bit ST |
| Panasonic Minas A6 | absolute_optical_multiturn | panasonic_a6 | 23-bit ST, batteryless MT |
| Allen-Bradley Kinetix 5700 (motor side) | absolute_optical_multiturn | hiperface_dsl | 24-bit (also accepts hiperface via 2198-K57CK kit) |
| Allen-Bradley Kinetix 5500 | absolute_optical_multiturn | hiperface_dsl | 24-bit |
| Siemens Sinamics S210 + S-1FK2 | absolute_optical_multiturn | drive_cliq | 22-bit ST + 12-bit MT |
| Beckhoff AX5000/AX8000 + AM8000 | absolute_optical_multiturn | oct_beckhoff | 18–24 bits ST/MT |
| Bosch Rexroth IndraDrive + MS2N | absolute_optical_multiturn | hiperface_dsl (also EnDat 2.2 option) | 24-bit ST/MT |
| Bosch Rexroth IndraDrive + MSK | absolute_optical_multiturn | hiperface (classic) | up to 18-bit + sin/cos |
| Schneider Lexium 32 + BMH/BSH | absolute_optical | sin_cos_1vpp + Hiperface (drive supports BiSS, SSI, Hall, EnDat 2.1/2.2, Resolver too) | 17-bit |
| FANUC αi-D series | absolute_optical_multiturn | fanuc_serial (HS variant on current gen) | up to ~32M counts/rev |
| Lenze i700 | resolver OR absolute (model-dependent) | resolver_analog or hiperface | 12-bit RDC or 17-bit Hiperface |

## 5. Industry-published taxonomies — what to align with

**No single dominant standard.** What exists:

- **IEC 61800-7** — defines drive **profile** semantics (CiA 402 /
  SERCOS / etc.), **not** an encoder taxonomy. Mode/state machine
  and object dictionary, not "is this an EnDat or BiSS encoder."
- **ETG.6010** (EtherCAT Technology Group) — Implementation Directive
  for CiA 402 over EtherCAT. Same scope as 61800-7: drive behavior,
  not feedback wire format.
- **CiA 402** — drive profile object dictionary. Defines "position
  actual value" objects (0x6064 etc.), agnostic about how the encoder
  physically reports.
- **Heidenhain encoder catalog** organizes top-level by **measuring
  principle**: optical / inductive / magnetic, then by **interface**
  (EnDat, 1Vpp, TTL, Fanuc serial, Mitsubishi serial, Yaskawa serial,
  Panasonic serial). This is exactly the device × protocol split we
  want.
- **Sick catalog** organizes by **interface family**: Hiperface,
  Hiperface DSL, SSI, analog. Same approach.
- **Drive vendor "supported encoder" lists** (Beckhoff AX5000, Rexroth
  IndraDrive) enumerate by protocol: EnDat 2.1/2.2, Hiperface, BiSS,
  SSI, 1 Vpp, TTL, Resolver. Confirms protocol is the right primary
  key for drive-side compatibility matching.

**We mirror Heidenhain's split** — `device` (measuring principle) ×
`protocol` (interface). This is the industry's de facto axis.

## 6. Open questions / things to flag rather than fabricate

- **Yaskawa Sigma-V vs Sigma-7 wire compatibility.** Both 20/24-bit
  absolute, both proprietary. Public sources don't say if a Sigma-V
  motor will hot-plug into a Sigma-7 drive. Treat as a single
  `yaskawa_sigma` enum value; the generation difference goes in the
  `raw` field if a customer needs it.
- **OCT vs Hiperface DSL.** Beckhoff OCT is "Hiperface-DSL-like" —
  same one-cable concept, electrically similar — but Beckhoff
  doesn't publicly say "we license DSL." Keep them as separate
  enum values; a drive that lists "Hiperface DSL support" may or
  may not accept an OCT motor and vice versa. Don't auto-collapse.
- **Renishaw inductive encoders (ASTRiA)** speak BiSS-C natively, so
  they fall under `device=inductive, protocol=biss_c`. The taxonomy
  already handles this without a special case.
- **"EnDat 3"** does not exist as a shipping product as of May 2026 —
  Heidenhain has hinted at a successor but no public spec. If a
  vendor's PDF says "EnDat 3", flag it for human review rather than
  coercing to 2.2.

## Sources

- [Heidenhain — EnDat 2.2 encoder characteristics](https://endat.heidenhain.com/endat2/encoder-characteristics)
- [Heidenhain — Interfaces of HEIDENHAIN Encoders, 02/2026](https://www.heidenhain.com/fileadmin/pdf/en/01_Products/Prospekte/PR_Interfaces_ID1078628_en.pdf)
- [Motion Control Tips — SSI / BiSS / Hiperface / EnDat compared](https://www.motioncontroltips.com/absolute-encoder-intefaces-differences-between-ssi-biss-hiperface-endat/)
- [Motion Control Tips — Hiperface vs Hiperface DSL](https://www.motioncontroltips.com/what-are-hiperface-and-hiperface-dsl/)
- [Sick Connect — 10 years of Hiperface DSL](https://sickconnect.com/reflecting-on-ten-years-of-hiperface-dsl-one-cable-technology/)
- [Mitsubishi MR-J5 User's Manual (Function)](https://dl.mitsubishielectric.com/dl/fa/document/manual/servo/sh030300eng/sh030300engl.pdf)
- [Yaskawa Sigma-7 batteryless encoder brief (CHEPS80000221A)](https://www.yaskawa.com/downloads/search-index/details?showType=details&docnum=CHEPS80000221A)
- [Panasonic MINAS A6 batteryless encoder brochure](https://mediap.industry.panasonic.eu/assets/download-files/import/ca_minas_a6_batteryless_encoder_pidsx_en.pdf)
- [Allen-Bradley Kinetix 5700 user manual](https://www.manualslib.com/manual/1501926/Allen-Bradley-Kinetix-5700.html)
- [SINAMICS S210 + SIMOTICS S-1FK2 operating instructions](https://support.industry.siemens.com/cs/attachments/109771824/S210_1FK2_op_instr_092019_en-US.pdf)
- [Beckhoff OCT — One Cable Technology overview](https://www.beckhoff.com/en-us/products/motion/oct-one-cable-technology/oct-one-cable-technology.html)
- [Bosch Rexroth IndraDrive Cs catalog (R911322210)](https://lsa-control.com/pub/media/pdf/Pool/r911322210_rexroth_indradrive_cs.pdf)
- [Schneider Lexium 32 catalog (March 2019)](https://iportal2.schneider-electric.com/Contents/docs/LEXIUM%2032%20AND%20MOTORS.PDF)
- [Mitchell Electronics — Fanuc High-Speed Serial Encoders](https://support.mitchell-electronics.com/hc/en-us/articles/4404719520027-Fanuc-High-Speed-Serial-Encoders)
- [TI TIDM-1011 — Tamagawa T-Format master interface design guide](https://www.ti.com/lit/ug/tidue74e/tidue74e.pdf)
- [Renishaw ASTRiA inductive rotary absolute encoder](https://www.renishaw.com/en/astria-inductive-rotary-absolute-encoders--49357)
- [Parker MB-series resolver-feedback motors](https://ph.parker.com/us/en/mb-series-general-purpose-brushless-servo-motors-resolver-feedback)
- [IEC 61800-7-301:2015 — Drive profile mapping](https://webstore.iec.ch/en/publication/23752)
- [ETG.6010 — Implementation Directive for CiA402 Drive Profile](https://www.ethercat.org/en/downloads/downloads_733ABB98E11545EA901D80D9A4CA7F80.htm)
- [CAN in Automation — CiA 402 series profile](https://www.can-cia.org/can-knowledge/cia-402-series-canopen-device-profile-for-drives-and-motion-control)
