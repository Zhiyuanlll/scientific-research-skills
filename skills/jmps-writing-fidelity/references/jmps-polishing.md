# JMPS prose polishing

Use this reference for manuscripts targeting *Journal of the Mechanics and Physics of Solids*, or when the user explicitly requests its style. It complements the main skill's scientific-writing principles. These are prose preferences distilled from representative mechanics papers and sentence-level language review, not publisher-mandated writing rules. Project-specific terminology, conclusions and protected passages apply only to their own manuscript.

## Mechanics argument and sentence structure

- Give the reader a clear connection between the physical quantity, mathematical relation, calculation and observation. Preserve a sound existing argument; a language-polishing request does not itself call for reconstructing the introduction or enlarging the contribution.
- Prefer a precise physical or mathematical object as the subject where it resolves ambiguity. A configuration or disregistry profile is relaxed; a center is sampled; a confidence interval is estimated from samples. Retain natural mathematical shorthand such as `Eq. (...) predicts` or `the kernel averages`; do not mechanically expand every inanimate subject into a passive construction.
- Let modifiers attach to their actual object. An additional comparison is not necessarily an additional configuration. The members of a sampling group remaining the same does not mean their coordinates remain fixed. Spell out these relationships only where needed.
- Reduce long chains of front-loaded modifiers using `of`, `from`, `with` or a clause. Preserve established technical names. Avoid synonym replacement that merely changes familiar terminology.
- Split sentences when the reader cannot distinguish definition, method condition and result. A colon can naturally connect a statement to evidence, and a semicolon can connect two closely related outcomes. Do not impose a 30-word limit or require one sentence for every computational action.

## Definitions, methods and derivations

- Define the mathematical object before discussing its consequences. Name the argument or derivative variable where it is necessary to distinguish a field from a curve or a variational traction from a spatial derivative.
- Place conditions beside the relation they qualify: fixed input statistics, reference geometry, asymptotic scale separation, boundary conditions, approximation order and sample selection. Preserve qualifiers such as `to leading order`, `approximately` and `over the sampled range`.
- Make derivation transitions specific: defining, substituting, averaging, normalizing or differentiating. Preserve signs, units and normalization. If `squared sum` may mean a different operation from `sum of squares`, report the competing readings instead of automatically replacing one with the other.
- Keep a sampling unit and its average visible. Distinguish averages within a configuration from averages across configurations, and pointwise confidence intervals from numerical threshold brackets. A single atomistic input configuration must not become an ensemble of independent atomistic inputs merely because multiple model realizations were generated from it.
- Preserve algorithmic distinctions needed to reproduce the method: internal versus endpoint images, target versus achieved tolerance, input clipping versus exact fitting, pairwise association versus independence of channels. Do not delete screening or solver failures to make methods sound smoother.

## Results, comparisons and interpretation

- State which quantity changes and with respect to which variable. In a spatial energy landscape, clarify displacement or separation when `rapid variation` could be read as a time rate; do not introduce a dynamics result.
- Retain comparison baselines and direction. `the combined profile minus the sum of the component profiles` specifies a signed residual; `difference between` may not. Expand a vague `stronger` or `the opposite` only when the intended comparator is clear from the existing evidence.
- Keep observations, model predictions and physical interpretations identifiable. `Correlates with`, `supports`, `is consistent with`, `can` and `may` encode different evidence levels. Preserve overall agreement alongside local deviations and reported exceptions.
- Keep validity attached to the result: a short-segment comparison does not establish every line length; a prescribed reference shape is not a relaxed configuration; a fitted central peak is not verification of an entire covariance function or a Gaussian distribution.
- Do not infer one additive observable from another. Energy-profile additivity, variance additivity and strength additivity require their own stated conditions. Preserve single-component and combined predictions as distinct objects.

## Figures, captions and cross-references

- Mix natural reference positions. A physical result may lead the sentence with `Fig.` placed beside its evidence, while a schematic or comparison may naturally start with `Fig.`. Avoid repetitive figure-by-figure narration without banning figure subjects.
- In captions, identify the plotted quantity, panels, essential conditions, symbols, normalization and sample/uncertainty definitions. Repeat the minimum context needed for independent reading; keep detailed interpretation in the body.
- Keep panel order and `respectively` mappings precise. Specify whether an error bar is a spatial SD, ensemble SE, confidence interval or numerical uncertainty.
- Preserve the division between the main article, its appendices and supplementary material. In supplementary text, an external appendix belongs to the main article; use an unambiguous phrase such as `Appendix ... of the main article` where needed.
- Inspect the document's actual label output before adding prefixes. Some classes or custom labels already include `Appendix`; `\ref{app:...}` must not acquire a second prefix. This is document-dependent, not a general property of every appendix label.
- Do not relocate scientific content or renumber items during a language-only pass. Preserve current reference targets and check nearby prose after any authorized replacement.

## Tense, person and review decisions

Use present tense for definitions, relations and observations represented in current figures, and past tense where appropriate for completed methods or reported earlier studies. Keep natural `We` for the authors' actions and `Here` for a useful definition. Do not require uniform past tense, delete all first-person statements or increase passive voice by default.

Preserve already natural sentences. For a full audit, use the three levels and comparison table in writing-principles.md; for a short request, do not impose that table. Distinguish a necessary correction from a rhythm preference. Preserve substantive uncertainty and scientific limits while removing discussion notes and unnecessary repetition.

## Representative style basis

The following representative papers informed the guide. The access descriptions record the material examined when the guide was developed; full texts are not included in this package. This reference does not require retrieving all papers for every polishing request or justify claiming their unread portions were inspected.

| Paper | Material examined when developing the guide | Reusable emphasis |
| --- | --- | --- |
| [Geslin and Rodney (2021), Microelasticity model of random alloys. Part I](https://doi.org/10.1016/j.jmps.2021.104479) | Published full text | Physical quantities, mathematical relations, atomistic comparison and conditions; distinction between variance, mean square and RMS. |
| [Geslin et al. (2021), Microelasticity model of random alloys. Part II](https://doi.org/10.1016/j.jmps.2021.104480) | Published full text | Separate correlation direction, sign and scale; keep local deviations when describing overall agreement. |
| [Wu and Zaiser (2025), Dislocations in the elastic fields of randomly distributed defects](https://doi.org/10.1016/j.jmps.2025.106264) | [arXiv preprint](https://arxiv.org/abs/2503.14692), not the journal final text | Identify statistical and sampling objects and core regularization conditions. |
| [Liu et al., Peierls–Nabarro modeling of dislocations in high-entropy alloys](https://doi.org/10.1016/j.jmps.2025.106457) | Publisher abstract and accessible indexed material | Connect atomistic inputs, the mechanical model and the scope of validation. |
| [Liu et al., Atomistically informed partial dislocation dynamics of multi-principal element alloys](https://doi.org/10.1016/j.jmps.2025.106478) | Publisher abstract and accessible introduction/conclusion portions | State a local field's physical role and how the mesoscale model incorporates it. |

Borrow these relationships and levels of precision, not published sentences or exaggerated novelty language. When the user asks for additional examples, quotations or source verification, inspect the actual requested sources and state which versions were accessible.
