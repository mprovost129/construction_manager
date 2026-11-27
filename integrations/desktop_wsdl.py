"""SOAP 1.1 document/literal contract matching Intuit's QBWC callback methods."""
from xml.etree import ElementTree as ET

from django.urls import reverse

from .desktop_protocol import INTUIT, METHODS
from .desktop_setup import public_base_url

WSDL = 'http://schemas.xmlsoap.org/wsdl/'
XSD = 'http://www.w3.org/2001/XMLSchema'
BINDING = 'http://schemas.xmlsoap.org/wsdl/soap/'


def wsdl_document():
    ET.register_namespace('wsdl', WSDL)
    ET.register_namespace('xsd', XSD)
    ET.register_namespace('soap', BINDING)
    root = ET.Element(f'{{{WSDL}}}definitions', name='HeritageDesktop', targetNamespace=INTUIT,
                      attrib={'xmlns:tns': INTUIT})
    schema = ET.SubElement(ET.SubElement(root, f'{{{WSDL}}}types'), f'{{{XSD}}}schema',
                           targetNamespace=INTUIT, elementFormDefault='qualified')
    array = ET.SubElement(schema, f'{{{XSD}}}complexType', name='ArrayOfString')
    ET.SubElement(ET.SubElement(array, f'{{{XSD}}}sequence'), f'{{{XSD}}}element',
                  name='string', type='xsd:string', minOccurs='0', maxOccurs='unbounded')
    for method, params in METHODS.items():
        for response in (False, True):
            name = method + ('Response' if response else '')
            element = ET.SubElement(schema, f'{{{XSD}}}element', name=name)
            sequence = ET.SubElement(ET.SubElement(element, f'{{{XSD}}}complexType'), f'{{{XSD}}}sequence')
            fields = [method + 'Result'] if response else params
            for field in fields:
                kind = ('tns:ArrayOfString' if response and method == 'authenticate' else
                        'xsd:int' if field in ('qbXMLMajorVers', 'qbXMLMinorVers') or response and method == 'receiveResponseXML'
                        else 'xsd:string')
                ET.SubElement(sequence, f'{{{XSD}}}element', name=field, type=kind)
            message = ET.SubElement(root, f'{{{WSDL}}}message', name=name)
            ET.SubElement(message, f'{{{WSDL}}}part', name='parameters', element='tns:' + name)
    port = ET.SubElement(root, f'{{{WSDL}}}portType', name='HeritageDesktopSoap')
    binding = ET.SubElement(root, f'{{{WSDL}}}binding', name='HeritageDesktopSoap', type='tns:HeritageDesktopSoap')
    ET.SubElement(binding, f'{{{BINDING}}}binding', transport='http://schemas.xmlsoap.org/soap/http', style='document')
    for method in METHODS:
        operation = ET.SubElement(port, f'{{{WSDL}}}operation', name=method)
        bound = ET.SubElement(binding, f'{{{WSDL}}}operation', name=method)
        ET.SubElement(bound, f'{{{BINDING}}}operation', soapAction=INTUIT + method, style='document')
        for kind, suffix in (('input', ''), ('output', 'Response')):
            ET.SubElement(operation, f'{{{WSDL}}}{kind}', message='tns:' + method + suffix)
            ET.SubElement(ET.SubElement(bound, f'{{{WSDL}}}{kind}'), f'{{{BINDING}}}body', use='literal')
    service = ET.SubElement(root, f'{{{WSDL}}}service', name='HeritageDesktop')
    ET.SubElement(ET.SubElement(service, f'{{{WSDL}}}port', name='HeritageDesktopSoap', binding='tns:HeritageDesktopSoap'),
                  f'{{{BINDING}}}address', location=public_base_url() + reverse('integrations:desktop_connector'))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
