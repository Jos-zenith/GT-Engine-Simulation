"""Read only the four operating inputs from the supplied XLSX using the standard library."""
import hashlib
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from cantera_injection import DEFAULTS,validate

def load_cases():
    root=Path(__file__).resolve().parent.parent
    source=root/'2635 boundary conditions.xlsx'
    if not source.exists():source=root/'data'/'raw'/'2635 boundary conditions.xlsx'
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(source) as book:
        shared=[]
        if 'xl/sharedStrings.xml' in book.namelist():
            shared=[''.join(node.itertext()) for node in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si',ns)]
        rows=ET.fromstring(book.read('xl/worksheets/sheet1.xml')).findall('s:sheetData/s:row',ns)
        def value(cell):
            node=cell.find('s:v',ns)
            if cell.get('t')=='s':return shared[int(node.text)]
            if cell.get('t')=='inlineStr':return ''.join(cell.find('s:is',ns).itertext())
            return node.text if node is not None else None
        headers={re.sub(r'\d','',cell.attrib['r']):value(cell) for cell in rows[0].findall('s:c',ns)}
        if not set(DEFAULTS)<=set(headers.values()):raise ValueError('Workbook does not contain the four required input columns.')
        cases=[];seen=set();populated_outputs=0
        for row in rows[1:]:
            values={headers.get(re.sub(r'\d','',cell.attrib['r'])):value(cell) for cell in row.findall('s:c',ns)}
            if not any(v is not None for v in values.values()):continue
            inputs=validate({key:float(values[key]) for key in DEFAULTS})
            unique=tuple(inputs[key] for key in DEFAULTS)
            if unique in seen:raise ValueError('Duplicate workbook operating point.')
            seen.add(unique)
            populated_outputs+=sum(v is not None for k,v in values.items() if k not in {*DEFAULTS,'S.NO'})
            cases.append({'case_id':int(float(values['S.NO'])),'inputs':inputs})
    return cases,{'file_name':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                 'row_count':len(cases),'populated_output_cells':populated_outputs,'input_columns':list(DEFAULTS)}
