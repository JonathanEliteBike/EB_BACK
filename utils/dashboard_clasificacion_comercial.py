"""Clasificacion comercial estatica SCOTT / ROPA para el Dashboard."""
from __future__ import annotations

import base64
import json
import zlib

# Generado desde clasificacion_final_productos.csv. No consulta archivos en runtime.
_DATOS_COMPRIMIDOS = (
    'c-pO<%aYs5l|_Grp|Nj7z8{`JB&DKF6{|s(e6J@S^tdM-'
    'J#+XI`m6fK8l_TOYF~hv%(X`fN!W{tbM{Ff9{~RT!Pb)fhd=)P!@qy{<A*Po!|{Ci+y3(W=`??t4wnyq`0yXgzt8*K%l!72|6KmE'
    '{PACJfBfSQACfhxrJermb=rNt{kl7U`aQsg>|*oRFVkVSzkGSweY{NTj3i)W0i#exE;ZnGgb)RcBw%C#qX-'
    'z4GoleNtiT^xz)<)U$23<%V;Ze(mdlsh$9KYfUF*R@GbiWM^KoA7z81AZY7f+*{L=~ZasM>^^@nQZ=;+&lI$f`)%k%E|^|F?&Xqt'
    'pSojxAdrEy%E#-({&TE?YyOd4}+w)gsUI-U=^^O}>L|EHVYTr>SpPygQ**UP8d@_*m8=l}Wd<>2px015HOm1meh+u&{OBOIR(uk-'
    'Z0KdvyG5iqQP;V8qV6paERX-QB(tODW{kf?xU1*9k-RRBo}vn8HlZ;Zlh$@O1k-wyW=93HJ}-'
    'ahP)&$s!sjK9vW*L8&FhyW4UTgMTCRwRh<xjo!yBPvF;2OI5JSdQ=<5g8(-_Uy#<cKm$V)+ea1L~j>Ec?t*=5Mc-'
    'c)H|rB?Jp>ROF1@c>H2b+Z@c%~^Oalsk_{s)!_?Y0SiT*nFIy83F?2TLHczMX+dy!eWMw2LBY7Dq$VgE}N*a>4S(Up`oQ&jUq#z?'
    'j8A+47xZGi?>|}Iyaw<|PvNp-iE>&5ZL}!<MyUU#BJ2wQr-BI4rho8pIWAcdPiJW{&#V|QJ_vG4RgX`sdI34HR=cmi{wmNJ6W0Z4'
    '$T>E}IFrR)utX&wHGD2<-4?fS+*W-D8?eGl{C?HZmqJT^Rg#s!9<mw6q&=z<@0hR(B1$YRE4_`z-U*_X-xBu%p&TDZ&hzp@Tl-bX'
    '(*I(w%`>ARvz*9h^fJ^}u0lc#qfV1EzAW%S}fWiRLuv%DZGtm}wrg7B+Zd|+!iwgN=N5r0p0};pZfORavZ;BVA6e6xboD30LB6dX'
    'ViI}7+_15&K^K{$n8B&%|iiA=*#XJANWqWs?4!^BaoP-'
    'i26w4{OW(~zlD27u)3<Y$Pp#pI<L~MzebXGYNaU^1LUCE@IEarhuWu0I#!C``D1fM%bd+!^^-'
    'u6LG`!$G5?JaLj$kLLaXi24znA>;7&4xr*IyVVDUv}50W&FE;y<LAlQ?_t|h!aJeB;sTdr-(RJ!>LBZu_BI>aJ(-'
    'fjwa4N7!k*cI8Kx=QNK||{YDk_8&%YAR87Cpu|etdzD4+VU3<rFvu$Y{m!@%P9+YNV!nib!OVhYC4~NN*Tf&c9!jD_Rk6Xfzhslk'
    'H$&H7}jfcsNhpD~5wJojV$uZ+nJ1%u&(qvulBHa3;OwKa7BXXZ2lNZ$ZcE>SYUyl3R?)x6Ub%;?w9Ebe0m%HP+vY)QO9Y;H#@iR&'
    'lKVeiDA(otA8*F=9;Pde^pAN^>#|9G-'
    'XCf}+VqYo{*GR;kh(WYNdu{FBFC%dv;&GF_O=#twXL9HL>V$X*a8)|cW$h3tAW=YuYr=3{d;y>sq|~<VZMw6|uDvnfzT6{uds+Iv'
    'yt2k;CTESWr13WYG&MA)Z$F{X&=cc@+#ZcxUti{FD}1@Vo1>u*Q176gp1+6sO!PK)?8mteh%AF|EvU2w76EGU)I=8QBRYW|!0~!L'
    'KHpB$dG~o4maUCr-Y`{yY0onDVX_3%9%%PrG$D4H5VyOXz7QJ?CTK846Jn>qXhQ5YAvT&~u$p49nqsh;VsN7VMw4)-'
    'Nx0M0l+)Cd7t~bp?fp83r`@M%f4j`TZOlBj_e-'
    '=9i6aU{*ml?3Mp%xZ8CgaUvF`?Oee{tAwa(biTj%xP%|Kg$whU`y=TK!eR@Q(x51()BIN#fEFPC*`9hSBoxh=KhQa3Ig4^tcuQyL'
    'Fb0%7vw(l9QK<I*%P&EpXs4^tWsQyLFb8V^$%3{&dfP<QW@Rk`;qe)E3!dcK{$Ot<4<jnnb=){XZ9zljSGS0HYe#?NQHqR&uYnZD'
    'f@HS`|pY5Wq@7ubFS^$zL-)Wi5C=-&kWo1uR*^lyg#%`|?{zZv>BL;q&z-'
    'wgemN&Hf1Pu{&>J2pm;?cVFIyxn<!m#^>NY4<&M<gv6<zYKg&`)TzB!q%JAbMoI_Ft$3cvH%JMw9S7HkbLgi#df-'
    'F&4gqJ^`7V#_m|!VtnQ+VsP)z*sLxPen7+gR^R&Ocz2toTmFn#&`v#!BI@1CK3dj)PX$w4!L3<v(6$9#nb|JL@h5|eVBnn_zSUU6'
    'KDIo_;$e7@894u}FO=f#r&U`w&O(<;W+uLdy`o#3Uz~47WZ#tfFo({)1{l=ySmpa~-b06V5h6eBVcWdIFEqQmuZdknB)A-'
    'I^>33G_+IvddjCKS%7*W8em{E)uFcdL$0!9*?ixDt_fKdcquY#BgYOZEMJOnjYlHgp7fDr_YB8aIIFa$McaZE$%c+>X#3sKkAaa_'
    'H0!%csGy`0zI+0jnpwzLjPmp;x6O1<fQ1pNICj$e=S?X+Ke<hu9O2u+R?ae|1GM4TexSP{pII4b{S5vNKx!59(8ia1Wh(d0c?Nqa'
    ')@DvlR%f`}7E`3fSAsy#`>$s!-B&Z~$xqW;H<`X4Lmf2^u5RPhn{(24p(C-Spb<xA8aC-'
    'R{a^<%DRd>BO6Q!nZ_yr?}v<ewnQmlxHk7uBh%|IzrzThp_Oun=yq&&Tx-'
    '{4V3a_JI5C!TRgd{L6IS`s(&Vu@Z`tP`rc^IK_9oJmuTA%ge?hO~-'
    '3s8u|qF1?oN2N2t$GUx_{hrzrJU+WX(WZQ$AZ(4YTl1LDhN{@d5-d^$|?zr0xHFQ@C%chz#x?*9(@4)6r9oB`6m)ngi-'
    ';<9;MTE?a0b8>EUPA-'
    '++Pii+dYM01%tt^D(Fd<PwHk1%6J`0Zt8OOomHsDC6fC<=>nR}8)GGYSuWX7J%WJ~OuJ~MqWQ178WLA`}~82<v}Ut#<!jlYBX2=x'
    'V?Kg@rH`LBI9wwv3RY^ug{M8=4Y*EKW|ks?Y>JQju{0!I`cPR9{~aB^Rm$Pv8EEH5*gxxu+F4EKfMzOcN^EH5+5!|5JK>iTAzj&G'
    'c{5s@P*Mg)%$E^tKQv2YxbcsLD52*SyI;khq7FSEeQEbuaO+**!X%X4daZY|F%HSll-'
    '8qVDDrjX6I8sv_5h1?TIB2MEkW2n8)!>)@X5rcO~bbKoH-'
    'Y+{MCehA@{BkD0jPi_{9f+9ZIg>nRPsF4!GRiZls*Jo%QdNk<O4owk+p6I6>G|_IB5*{8i0p#kL>xy%j_@3jIHF=i<>9Q{T9wD5a'
    '%)wd)XJ?@ahVy@mDKBbT^xzH5V0X*PsE9cD-hf9>q73>0d-I8i8vB*?`qOrliQ-'
    '8I4hxe3B^e$BsFgQ1}VGLxg+%W>&y8ve|seEUv`o#jDQ)YcW3IIcNi{^B!EOkOQNDBy}L;7L$cy@6fKF0mISYW6vgQ%S`rm4>D`T'
    'eA5s;kqi9J~v?N4DON;`dXh~AEBq_3!6jhuRr;`;0m=rx*lB8JC6=cQ5D@%N$xB@0cSCADc&WaRgMNEpKc8Vg!Md8>g$&R8^DvH`'
    'E3ZE2(?W>}D%!(8jMT(1}cB-Uy6oD-'
    'Y$5w@7tHQBWk{yBVt#Jy7BqmwW5~F~41w@kKqG*X#K!O4yNO5#l0dWe5S3m@T4T{={RstysNL2(jDIgNZDyrBidV8<v9=)PJ@rpj'
    'yD>@|~6_BKWD6W8BVK&9|vJH~#C~U8o8McZkG8-h>QPhrN^51sP_<dW-m`?L5#48|H0SO9-'
    'Q$V5uk`$0CfF#N3WJOC9j!g>3CP{XRq9uyjNrLR8-a*sh_-Xn7!zv<kM4^aS1&GQ|#BhY=2*(kgBLYW6p47k*f>tDspk)?AOdKI-'
    'Md65w5y^0b<p{?So+AQBM2_I@Ox&G`yEAcjX70|+-'
    'I=*NGk0g^?#$es?cK!R#k<3NTKj%(Xz#&o5(*|%N@zcG&?Hz)aG2mR0q3INTvVKkigS^1U1VGr8RsI?Tts6q!D52L1X>rdy;HbJN'
    'SKf@p<n`bl*f+p*ijxk%HmvDoC}L{;b1P3v3zbm8JiT4B7iuffH=kJL<J-'
    '(AXO0)tAKa~MB!Mc2&`9_%`0N!6$R)Nj&+JE_6oCkg-'
    '^V~Cr*+bMHPER&*l|A@d}>=MePJdilf4@iYFdyR<tB2dbXe_z@YF+RM=keXqqnyvjs%~21S1o6$PkxzRed&f1>E9qoQ`AqIMJy$$'
    '7#4@nyW$c{-'
    'ox>E&tl(;%_;s&fyKIihex#faYS`5wY@gyRU$5!@G%hcohUMjp<{!<l%cMqVS4*GS?SNdk8!j^Mpk5QMYyL^i+e6nhsz?+^+mR7&'
    'Vw0lh=8nBXwMV?w}$hzZz}8GABgPiE}Nf<0NVCkysu!JaJGlLdRSU{4n8$$~vuuqP|@WX9E<adl^0-'
    '5FPR#?_ti6({2>PR3W9jITHuUvV<-Co}FRGwvrd?k6+vCzH3B;4r~sLcjzZNt-YsV?x0Mdc{f3U;>V$!;y42l8#3b*QUd@>2Pg2T'
    '$>KpCLT2>JZesO)SU3BIpHyH!eicq$Gi!Tc~io1NH`7&$06Z3;1No~Ba~F}^`hcjRGf>7b5U_GS8*>_aW7Y~qbhY&HU<+cCOAy+m'
    '=G`_VnV_M>dD-G>-6onm$lamFVZkBjpNcZF3sc8GA^yd()QNbZK)lXx^d~ao7}jY+TVlvIPL%ZGM}Dr>-x&{?T;xn^a<)S)3-'
    'ku*U($oeh2j)wjcg}puZ3EA87tz{j|Rp*wDlJ3ABEp|0DAMQvP_(H{o@Ngx4VwUWZ6{9U|d%h=ga)6P`U!c=kMEM@8(Yh#eKNqat'
    ';b3lS6OS-'
    '2Q5fv+mzY!;l&f<1|6Wb%)ve&^%<Y5MDrK7RGm@MHR=?{NRv;qkG<(_@F{#||%#9bO+fycqk~;qI}+{bPrR#}1E=9iARLJU@1LdF'
    '=4V()8o=S$&7IJMUsW)CZ`KP@kYaLw$jIcIRE3fqDz|(0>m4&q4n==sySj=Q11l^zN;{y>7hOtM~b>Z9t@eL;;xs3I$XM=zX$a8{'
    'iONF=B;)-shyZ0hR(-3{w9~{I_3fnXEMmNDx3wR<y(_AW?BTMbQ$cfGB)os-'
    'h)c0g?DbksYTv9j9oCmjqUkVyEZ|oT4RO(h^1Y=oI~lSG2?{TH+LatW$JKUeOY-Xo*vF)K1Z}dBGdrJ)X<;cxK+?S#gh7Nj!d1!s'
    '9g#kDrwA_&EoUpHlGnxfh?Yqwo_W9zXBlGxsE&SNHho9gm+m@%Wh&kDod5^qG@n=^ALVw7M4pJ_}l#OB^b0gTsV~2|Swy6Fep)Or'
    'Y8HbPaSN;5I~q2^JF^CU{H;m=JL`aT@|}L&5}l4-<^w+dRPp1tcmUg0BSm8Wj*pOR@q|6p*UOj!{6Y0-'
    '_il)3@m_gn$W!5~{;(u$T}rq2f4r+y)-'
    'WgwFzdvT{%INM?K%*pn4|vL?8mRx<^#h1ukKr`?_}yX(_szU^Oc*HumtajJ&XI~myG^bU@ioFw9i;?p}B(rix<aYQ~WqP!PX-lK>'
    'ks;}N@v?fQC_x6p!Y5AA6-q*G7j5ek1i^sM!j5j!qOY3-p-'
    'MADaCy)QC8JGHT>1cAYZ*OtDo{sZ8t&1xVw>LN569*!WM4ZOO9Ur8+CoV(`Qq}QgwtHer#EzuO5;6K^KP*nZ{e7qF>&rat{xt8to'
    'L;W0jP}=+Iv7E~@B&8aWvt5EkMG`<`^<jbvR|)6?%E%QoL+Co=iB=mgIDww>3#dda}B+R`Uuazz~47e@1Q<FeS-'
    'Q7^)&v_zZT`MnD&SE=F4>0y{&*8_q&gmX`Nyv6epp01*QE+`!~w%IDOe>BmpCHMl_s}tbpMJ3@?a>ATKEh7y^$a!MWt-xqP{O+|B'
    'D9NRC9DhQ*=d`<!1d=i9o}k4wY2G>%KtxHOMT%b;}0ERfb=X?qyBEw$rPH!dADV$JP4r;q39a9q=t_SJ2gf884IEb@fp?brR>|Mk'
    'j#-LPM`OzxOGGkIZhn7F_ahaw+b-xYg*`SP-^k5HeWJ~Msio&L7oLcN1}&-'
    'Cq=l)c?+Y|A~92PTh5Uf8*}Cx!3J3zJuN?gq&dOI%`!OR({l$(d~uE1AS>lbCHXihOLpSY$uHUVoX_#fgYB5f>nCFGy^OL&{w@HZ'
    'SMb>zcY)q>e<LD^TZljeI?Qy{sz3uriG)<4;fHw~AsnUsnN+0z3so3P=oKYCm9g#%3X8OemO8A;B8M2qCu#`{VO%KJ7QkFPe5Q|L'
    'OJiIv;m0^I9pTR7ls6NY{~i#=)O2oAQ{L+%S1&a>wK-bTRf1<+jZ4y85x})BMYH-'
    'i8DPBncqF;)sNd5$*ZPb}M*l18;>ZkL2V$?T>4ec8y(MF7s`7o2S!xcU-'
    ';Xn_}pdqIgWeSIk1MRxaYR$oQIDEG7g@;P)QbQI+SS;y5@=h?v0d;;^GCb(GD6i#GA6XYC(W4z9a|+}`l=dA?k}9ydpP?Hxl6Kzk'
    '6@YC)iYNCAlg3Iw>mt%Ix5G%n5K(lRcs!%}a?e>DzEJ9cfqyguJ9yFa}?pDy$2uN#W!nC$qD`19$oI?Yl?CurKY!nbLAc+&AXmiu'
    'z{>+Rd$`@imy+>e(@825VT9@<r@8<&pvVPU*DdpBKgW+)<k$1A(%{ri5W^QTpPf%*jX73p((Ff?6Xj{Dp0^YOg9t|HoZeN9CB*0b'
    'FT&s$+QBJvZ#ttjp5>@rB--%gk34Og{S=No|b;>~<IOwaq{s@|KPJF?^RbU3b0G=x&S>)M*#b+v1#l_F|;O1X*1>|F|>wio-'
    '~XKcPbcD~w(*s<sLC&ntkQh-B%P}{qW-T|M^m%r@(dV1MNM|(!E3-'
    'eGswir&p@B&8Q1`84Tqc<<xfJ^}eI<!5o`rcEli95RdJK``dF0o_uJ$*gi^-'
    'yV#5_%v7L!xPC;b+L&Y*67d_U#Gg2EyB}QSZE_>>BK@D{bEr!S(5MJRepG?eWVu?e2VCmACiAFLzFB19V?fdf=Aht!rO{wC517m-'
    'FFtTn%5xgP)D?b&OYAgr;p?=F1&@d)V91Tc~$X@1Z_G{R3b7@*RtV+kP(=`u!jO3r_%%{Q'
)

CLASIFICACION_COMERCIAL_POR_PRODUCT_ID = {
    int(product_id): valor
    for product_id, valor in json.loads(
        zlib.decompress(base64.b85decode(_DATOS_COMPRIMIDOS.encode())).decode()
    ).items()
}

if len(CLASIFICACION_COMERCIAL_POR_PRODUCT_ID) != 1484:
    raise RuntimeError("El mapeo comercial SCOTT / ROPA est� incompleto.")


# Validado comercialmente en la auditoría específica de Submodelo. Estas llaves
# son product_id de Odoo; no se infieren a partir de SKU, talla, color o texto.
SUBMODELOS_APROBADOS_POR_PRODUCT_ID = {
    14532: "VOLT EVO CLIP BOA",
    14533: "VOLT EVO CLIP BOA",
    14534: "VOLT EVO CLIP BOA",
    14535: "VOLT EVO CLIP BOA",
    14536: "VOLT EVO CLIP BOA",
    14537: "VOLT EVO CLIP BOA",
    14538: "VOLT EVO CLIP BOA",
    18078: "VOLT EVO CLIP",
    18079: "VOLT EVO CLIP",
    18080: "VOLT EVO CLIP",
    18081: "VOLT EVO CLIP",
    18082: "VOLT EVO CLIP",
    18083: "VOLT EVO CLIP",
    17997: "VOLT EVO FLAT",
    17998: "VOLT EVO FLAT",
    17999: "VOLT EVO FLAT",
    18000: "VOLT EVO FLAT",
    18016: "VOLT EVO FLAT BOA",
    18017: "VOLT EVO FLAT BOA",
    18018: "VOLT EVO FLAT BOA",
    18019: "VOLT EVO FLAT BOA",
    18020: "VOLT EVO FLAT BOA",
    5871: "PRO SHIELD METAVERSE",
    17987: "MTB RC ULTIMATE",
}


def obtener_clasificacion_comercial(product_id: int | None) -> dict | None:
    """Obtiene una clasificaci�n validada por product_id, sin inferencia textual."""
    return CLASIFICACION_COMERCIAL_POR_PRODUCT_ID.get(product_id)


def obtener_submodelo_aprobado(product_id: int | None) -> str | None:
    """Obtiene exclusivamente el Submodelo validado para un product_id."""
    return SUBMODELOS_APROBADOS_POR_PRODUCT_ID.get(product_id)

