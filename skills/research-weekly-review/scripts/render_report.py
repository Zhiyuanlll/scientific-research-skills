#!/usr/bin/env python3
"""Render LaTeX math into visible local images while preserving source Markdown.

XeLaTeX and pdftoppm are required for equations. Optional local marked/Node
produces a self-contained HTML companion. No network or research calculations.
"""
import argparse
import base64
import hashlib
import html
import json
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote


MATH = re.compile(
    r"\$\$(?P<display>.*?)\$\$|\\\[(?P<bracket>.*?)\\\]|"
    r"\\\((?P<paren>.*?)\\\)|(?<!\\)\$(?!\$)(?P<inline>[^\n$]+?)(?<!\\)\$(?!\$)",
    re.DOTALL,
)
PROTECTED = re.compile(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$|`[^`\n]+`")


def run(command, cwd=None, timeout=60):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed:\n{(result.stdout + result.stderr)[-3500:]}")
    return result.stdout


def render_equation(tex, display, assets, font, engine, dpi):
    if not tex.strip():
        raise ValueError('Empty mathematical expression')
    if re.search(r'[{}\\]', font):
        raise ValueError('Font name cannot contain TeX commands')
    key = hashlib.sha256(json.dumps([tex, display, font, engine, dpi]).encode()).hexdigest()[:18]
    prefix = assets / ('equation-' + key)
    png, pdf = prefix.with_suffix('.png'), prefix.with_suffix('.pdf')
    if png.is_file() and pdf.is_file():
        return png, pdf
    if not shutil.which(engine) or not shutil.which('pdftoppm'):
        raise RuntimeError('Equation rendering requires XeLaTeX and pdftoppm; source preserved')
    if re.search(r'\\(?:tag|label)\b', tex):
        content = r'\begin{minipage}{18cm}\begin{equation*}' + tex + r'\end{equation*}\end{minipage}'
    else:
        content = '$' + (r'\displaystyle ' if display else r'\textstyle ') + tex + '$'
    document = '\n'.join([
        r'\documentclass[11pt,border=2pt]{standalone}',
        r'\usepackage{fontspec,amsmath,amssymb,bm}',
        r'\setmainfont{' + font + '}',
        r'\begin{document}', content, r'\end{document}',
    ])
    with tempfile.TemporaryDirectory(prefix='math-', dir=assets) as temp:
        folder = Path(temp)
        (folder/'equation.tex').write_text(document, encoding='utf-8')
        run([engine, '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error',
             'equation.tex'], cwd=folder)
        generated = folder/'equation.pdf'
        if not generated.is_file():
            raise RuntimeError('TeX returned without a PDF; source preserved')
        run(['pdftoppm', '-singlefile', '-png', '-r', str(dpi if display else 120),
             str(generated), str(folder/'equation')])
        shutil.copy2(generated, pdf)
        shutil.copy2(folder/'equation.png', png)
        prefix.with_suffix('.tex').write_text(document, encoding='utf-8')
    return png, pdf


def render_markdown(source, output, font='Times New Roman', engine='xelatex', dpi=240):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError('Source and rendered Markdown must be different files')
    text = source.read_text(encoding='utf-8')
    if re.search(r'(?m)^\s*```(?:latex|tex|math)\s*$', text):
        raise ValueError('Write report equations as math, not fenced LaTeX code')
    output.parent.mkdir(parents=True, exist_ok=True)
    assets = output.parent/'assets'
    assets.mkdir(exist_ok=True)
    protected = []

    def protect(match):
        protected.append(match.group(0))
        return f'\x00PROTECTED{len(protected)-1}\x00'

    text = PROTECTED.sub(protect, text)
    formulas = []

    def replace_math(match):
        display = match.group('display') is not None or match.group('bracket') is not None
        expression = next(v for v in match.groupdict().values() if v is not None).strip()
        png, pdf = render_equation(expression, display, assets, font, engine, dpi)
        formulas.append({'latex':expression, 'display':display, 'image':str(png), 'pdf':str(pdf)})
        image = f'![公式{len(formulas)}]({png})'
        return '\n\n'+image+'\n\n' if display else image

    text = MATH.sub(replace_math, text)
    for index, original in enumerate(protected):
        text = text.replace(f'\x00PROTECTED{index}\x00', original)
    # Resolve existing report images, including relative paths containing spaces.
    image_pattern = re.compile(r'!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)')
    images = []

    def resolve_image(match):
        target = match.group(2).strip().strip('<>')
        if re.match(r'https?://|data:', target):
            raise ValueError('Use verified local images for a self-contained research report')
        path = Path(target)
        if not path.is_absolute():
            path = source.parent/path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError('Missing report image: '+str(path))
        images.append(str(path))
        return f'![{match.group(1)}](<{path}>)'

    text = image_pattern.sub(resolve_image, text)
    output.write_text(text, encoding='utf-8')
    metadata = {'source':str(source), 'markdown':str(output), 'formula_count':len(formulas),
                'image_count':len(images), 'non_formula_image_count':len(images)-len(formulas),
                'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'formulas':formulas, 'images':images}
    (output.parent/'render_manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    return metadata


def render_html(markdown, output, node, marked_module):
    if not node or not marked_module:
        raise ValueError('HTML requires explicit local Node and marked module paths')
    code = r'''
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const { marked } = await import(pathToFileURL(process.argv[1]).href);
process.stdout.write(marked.parse(fs.readFileSync(process.argv[2], 'utf8')));
'''
    body = run([node, '--input-type=module', '-e', code, str(marked_module), str(markdown)])

    def embed(match):
        path = Path(unquote(html.unescape(match.group(1))))
        if not path.is_file():
            raise FileNotFoundError('HTML image missing: '+str(path))
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        return 'src="data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()+'"'

    body = re.sub(r'src="([^"]+)"', embed, body)
    style = 'body{max-width:960px;margin:32px auto;padding:0 24px;font:17px/1.7 serif;color:#222}img{max-width:100%;height:auto}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}pre{white-space:pre-wrap}a{color:#235f8c}'
    Path(output).write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>科研周报</title><style>'+style+'</style><body>'+body+'</body></html>',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--html', type=Path)
    parser.add_argument('--font', default='Times New Roman')
    parser.add_argument('--tex-engine', default='xelatex')
    parser.add_argument('--dpi', type=int, default=240)
    parser.add_argument('--node')
    parser.add_argument('--marked-module', type=Path)
    args = parser.parse_args()
    if args.dpi < 120:
        parser.error('dpi must be at least 120')
    result = render_markdown(args.source,args.output,args.font,args.tex_engine,args.dpi)
    if args.html:
        render_html(args.output,args.html,args.node,args.marked_module)
        result['html'] = str(args.html.resolve())
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
