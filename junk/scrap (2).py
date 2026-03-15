mircre = re.compile('('
                      '(?:'
                        '(?:\x03(?:1[0-5]|0?\\d)'
                          '(?:'
                            ',(?:1[0-5]|0?\\d)'
                          ')?'
                        ')'
                        '|'
                        '\x02'
                        '|'
                        '\x1F'
                        '|'
                        '\x16'
                      ')'  
                      '|'
                      '^'
                    ')'
                    '([^\x03\x02\x1F\x16]*)')

mircre = re.compile(""" 
                      ( 
                        (?:
                          \x03\\d{1,2}
                          (?:,\\d{1,2})?
                        )
                        |\x02|\x1F|\x16|^
                      )
                      ([^\x03\x02\x1F\x16]*)  #note: a \x03 with no numbers after it will get dropped off the face of the earth
                    """, re.VERBOSE)  


mircre = re.compile(""" 
                      ( 
                        (?:
                          \x03
                          (?:
                            (?P<fg>\d\d?)
                            (?:,(?P<bg>\d\d?))?
                          )?
                        )
                        |\x02|\x1F|\x16|\x0F|^
                      )
                      ([^\x02\x1F\x16\x03\x0F]*)
                    """, re.VERBOSE)  


