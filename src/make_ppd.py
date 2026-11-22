"""Generate the local queue description, wire encoder profile and loopback IPP attributes."""
from hp_profile import PAPERS, MEDIA, PT
from pathlib import Path

def ppd_header(title):
    return f'''*PPD-Adobe: "4.3"
*FormatVersion: "4.3"
*FileVersion: "2.0.0"
*LanguageVersion: English
*LanguageEncoding: ISOLatin1
*PCFileName: "HP115NW.PPD"
*Manufacturer: "HP"
*Product: "(HP Laser MFP 115nw)"
*ModelName: "{title}"
*ShortNickName: "HP 115nw SPL3"
*NickName: "{title}"
*PSVersion: "(3010) 0"
*ColorDevice: False
*DefaultColorSpace: Gray
*LanguageLevel: "3"
*Throughput: "20"
*cupsVersion: 2.0
'''

def choices(key, label, values, default):
    return (f'*OpenUI *{key}/{label}: PickOne\n*OrderDependency: 20 AnySetup *{key}\n*Default{key}: {default}\n'
            + ''.join(f'*{key} {name}/{txt}: "{code}"\n' for name,txt,code in values)
            + f'*CloseUI: *{key}\n')

def ppd(encoder, custom=(100,150)):
    s=ppd_header('HP Laser MFP 111-117' + (' SPL3 Renderer' if encoder else ''))
    if encoder:
        s+='''*cupsFilter: "application/vnd.cups-raster 0 rastertoqpdl"
*General DocHeaderValues: "<0><0><1>"
*QPDL QPDLVersion: "3"
*QPDL BandSize: "128"
*QPDL PacketSize: "512"
*QPDL SpecialBandWidth: "True"
*PJL BeginPJL: "<1B>%-12345X"
*PJL EndPJL: "<09><1B>%-12345X"
'''
    else:
        s+='''*cupsFilter2: "application/vnd.cups-pdf application/pdf 0 -"
*cupsManualCopies: False
*VariablePaperSize: True
*MaxMediaWidth: "612.283"
*MaxMediaHeight: "1009.134"
*HWMargins: 12 12 12 12
*NonUIOrderDependency: 10 AnySetup *CustomPageSize
*CustomPageSize True: "pop pop pop <</PageSize [5 -2 roll]/ImagingBBox null>>setpagedevice"
*ParamCustomPageSize Width: 1 points 215.433 612.283
*ParamCustomPageSize Height: 2 points 360 1009.134
*ParamCustomPageSize WidthOffset: 3 points 0 0
*ParamCustomPageSize HeightOffset: 4 points 0 0
*ParamCustomPageSize Orientation: 5 int 0 0
*APPrinterPreset Normal/600 dpi: "*cupsPrintQuality Normal com.apple.print.preset.quality mid"
*APPrinterPreset Best/1200 dpi: "*cupsPrintQuality High com.apple.print.preset.quality high"
'''
    dims={k:(v[1],v[2]) for k,v in PAPERS.items()}
    if encoder:
        dims["UserSheet"]=custom
    for key in ('PageSize','PageRegion'):
        s+=choices(key,key,[(k,k,f'<</PageSize [{w*PT:.3f} {h*PT:.3f}]/ImagingBBox null>>setpagedevice') for k,(w,h) in dims.items()],'A4')
    s+='*DefaultImageableArea: A4\n*DefaultPaperDimension: A4\n'
    for k,(w,h) in dims.items():
        margin=13.2 if k=='Monarch' else 12.0
        s+=f'*ImageableArea {k}: "{margin} {margin} {w*PT-margin:.3f} {h*PT-margin:.3f}"\n*PaperDimension {k}: "{w*PT:.3f} {h*PT:.3f}"\n'
    s+=choices('ColorModel','Output Color',[('Gray','Black and White','<</cupsColorSpace 3/cupsColorOrder 0/cupsBitsPerColor 1/cupsCompression 17>>setpagedevice')],'Gray')
    s+=choices('cupsPrintQuality','Print Quality',[('Normal','600 x 600 dpi','<</HWResolution [600 600]/cupsInteger1 4>>setpagedevice'),('High','1200 x 1200 dpi','<</HWResolution [1200 1200]/cupsInteger1 5>>setpagedevice')],'Normal')
    s+='*DefaultResolution: 600dpi\n'
    s+=choices('MediaType','Media Type',[(k,k,'') for k in MEDIA],'OFF')
    s+=choices('InputSlot','Paper Source',[('Auto','Automatic',''),('Upper','Tray 1','')],'Auto')
    if encoder:
        for key, options, default in [('EconoMode',['ON','OFF'],'OFF'),('EdgeEnhance',['OFF','NORMAL','MAXIMUM'],'NORMAL'),
                                      ('Altitude',['LOW','HIGH1','HIGH2','HIGH3'],'LOW')]:
            s+=choices(key,key,[(v,v,'') for v in options],default)
    else:
        from settings import BINDING_CHOICES, SETS
        # macOS owns the labels of the standard Duplex control. A driver option
        # makes both edges explicit while keeping existing system presets usable.
        s+=choices('HPDuplexMode','Double-sided (Manual)',
                   [('auto','Use Double-sided Setting','')]+[(mode,label,'') for mode,label in BINDING_CHOICES],
                   'auto')
        exposed={'out':'HPOut','reload':'HPReload','head':'HPHead','econ':'HPEcon','edge':'HPEdge','text':'HPText','black':'HPBlack','layout':'HPLayout','nup':'HPNup','border':'HPBorder','subset':'HPSubset','reverse':'HPReverse','skip':'HPSkip'}
        labels={'out':'Output face','reload':'Reload printed face','head':'Page top at output','econ':'Toner saving','edge':'Edge enhancement','text':'Text darkening','black':'All text black','layout':'Page layout','nup':'Pages per side','border':'Page border','subset':'Page set','reverse':'Reverse pages','skip':'Skip blank pages for simplex'}
        for _, fields in SETS:
            for key, _, opts, default in fields:
                if key in exposed:
                    s+=choices(exposed[key],labels[key],[(v,v,'') for v,_ in opts],default)
        binding_labels = dict(BINDING_CHOICES)
        s+=choices('Duplex','Double-sided',[
            ('None',binding_labels['none'],'<</Duplex false>>setpagedevice'),
            ('DuplexNoTumble',binding_labels['long'],'<</Duplex true/Tumble false>>setpagedevice'),
            ('DuplexTumble',binding_labels['short'],'<</Duplex true/Tumble true>>setpagedevice'),
        ],'None')
    return s

def write_build(dest):
    dest.mkdir(exist_ok=True,parents=True)
    (dest/'queue.ppd').write_text(ppd(False))
    (dest/'renderer.ppd').write_text(ppd(True))
    import json
    (dest/'media.json').write_text(json.dumps([v[-1] for v in PAPERS.values()]))

if __name__=='__main__':
    write_build(Path(__file__).resolve().parents[1]/'build')
